#
# This file is part of the wwpump distribution
# Copyright (c) 2022 Martin Köhler.
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, version 3.
#
# This program is distributed in the hope that it will be useful, but
# WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the GNU
# General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program. If not, see <http://www.gnu.org/licenses/>.
#
import micropython, onewire, ds18x20
import sys, io, time
import ulogging
import timetable
from machine import Timer
from machine import Pin
from led import Led, RGB_led, Singleton
from ulogging import info, debug
from My_time import my_time
# DS18B20
DS18B20_PIN = 22
DS18B20_INDEX = 0 # Only one sensor
# Pumpe
PUMPEN_PIN = 20
# Heartbeat (ms)
TICK_TIME = 1000 # Main routine
# All thess times are in s
WAITING_TIME = 15*60 # Pump should only run every 15 minutes
RUNNING_TIME = 40 # Pump runs for 40 seconds
QUIET_TIME = RUNNING_TIME + 20 # Rising temperatgure will be ignored in QUIET_TIME
HOLIDAY_TIME = 24 * 60 * 60 # Holiday mode if no request for 24h
DESINFECT_TIME = 3*24*60*60 # Run pump at least every 3 days & do Backup
# USR Button
USR_PIN = 13
# Backup
LOG_FILENAME = "wwpumpe.log"
class Alarm_timer():
    timer3 = Timer()
    def __init__(self, pumpe):
        self.pumpe=pumpe
        self.ttable = self.pumpe.ttable
        self.pumpe_tick_ref=pumpe.tick
        self.pumpe_desinfect_ref=self.pumpe_desinfect
        self.pumpe_scheduled_run_ref = self.pumpe_scheduled_run
        self.timer1= Timer(period=TICK_TIME, mode=Timer.PERIODIC, callback=self._cb1) # Worker
        self.timer2=Timer(period= DESINFECT_TIME * 1000, mode=Timer.PERIODIC, callback=self._cb2) # Alle 3 Tage
        # Make sure we initialize the alarm scheduler (timer3)
        self.schedule_next_alarm(self.ttable)
    def schedule_next_alarm(self, ttable):
        alrm = ttable.next_alarm() # This is in seconds
        self.timer3.deinit() # Just to be on the safe side
        if alrm == False:
            info(f"{timetable.pt()}: No next alarm scheduled")
            self.timer3_time = False
            return
        # Start QUIT_TIME seconds earlier to ensure that a periodic request near a slot boundary is handled 
        # correctly. If request is always at 8:15:01, the pump is started so that the next request at 8:15:01 
        # is recognized as a Warm water request, which means it must be after the QUIET_TIME
        if alrm > QUIET_TIME:
            # Ensure that alrm remains > 0
            alrm -= QUIET_TIME
        self.timer3 = Timer(period=alrm*1000, mode=Timer.ONE_SHOT, callback=self._cb3) # need ms here
        self.timer3_time = my_time()+alrm # Store this in the class
        info(f"{timetable.pt()}: Next scheduled_run at: {timetable.pt(self.timer3_time)}")
    def pumpe_scheduled_run(self, args=None):
        self.pumpe.scheduled_run()
        self.schedule_next_alarm(self.ttable)
    def pumpe_desinfect(self, args=None):
        # If we do not have a next alarm scheduled, check whether there is a new
        # entry in the timetable
        if not self.timer3_time:
            self.schedule_next_alarm(self.ttable)
        # Start the desinfect run
        self.pumpe.desinfect()
    # For debugging
    def set_pumpe(self, pumpe):
        self.pumpe=pumpe
    def stop(self):
        self.timer1.deinit()
        self.timer2.deinit()
        self.timer3.deinit()
    # These call backs are interrupt driven, hence complicated functions are not allowed
    # We use micropython.schedule to start the "real" worker
    # We are not allowed to allocate memory in the ISR See
    # https://docs.micropython.org/en/latest/reference/isr_rules.html#isr-rulese
    def _cb1(self, tim):
        micropython.schedule(self.pumpe_tick_ref, tim)
    def _cb2(self, tim):
        micropython.schedule(self.pumpe_desinfect_ref, tim)
    def _cb3(self, tim):
        micropython.schedule(self.pumpe_scheduled_run_ref, tim)

class Temp(Singleton):
    """
    Temperature class:
    Stores the last temperatures and checks for rising temperature
    """
    cnt = 0
    led_onboard = Led() # On board led
    def __init__(self):
        try:
            ow = onewire.OneWire(Pin(DS18B20_PIN)) # create a OneWire bus on GPIO22
            self.ds = ds18x20.DS18X20(ow)
            self.rom = self.ds.scan()[DS18B20_INDEX] # Only one sensor
        except IndexError: # No sensor found
            info(f"{timetable.pt()}: No DS18B20 sensor found. Will use mock up")
            self.rom = False
            class ds():
                temp = 22.0
                def convert_temp(self):
                    return
                def read_temp(self, rom):
                    return self.temp
                def set_temp(self,value):
                    self.temp = value
            self.ds = ds()
        temp_now = self._get_temperature()
        self.t = [temp_now] * 5 # Initialize history

    def rising(self):
        """
        Returns true if there the temperature is higher
        than 5 measurements before
        """
        self.cnt = (self.cnt + 1) % 5 # Current slot in the buffer
        temperature = self._get_temperature()
        self.t[self.cnt] = temperature
        cnt_alt  = (self.cnt + 2) % 5 # 5 measuremenzs earlier
        temperatur_delta = self.t[self.cnt]-self.t[cnt_alt]; # Temperaturdifferenz der letzten
                                                             # 5 Messzyklen bzw. Sekunden
        if (temperatur_delta >= 0.12): # 0.12°
            info(f"{timetable.pt()}: Rising temperature: {temperature}")
            self.led_onboard.blink(num=2)
            return True
        return False

    def _get_temperature(self):
        self.ds.convert_temp()
        # Warten: min. 750 ms
        time.sleep_ms(800)
        return self.ds.read_temp(self.rom)

class Pumpe():
    holiday = False
    pumpe_laeuft = False
    def __init__(self):
        self.temp = Temp()
        self.rgb_led = RGB_led()
        self.ttable = timetable.Timetable()
        self.led_onboard = Led()
        self.pumpenpin = Pin(PUMPEN_PIN, Pin.OUT)
        self.pumpenpin.on() # Low -> Pumpe ein
        self.now = my_time()
        self.last_pumpenstart = self.now - WAITING_TIME
        self.rgb_led.set(RGB_led.off)
        self.last_scheduled_run = self.now - (WAITING_TIME + QUIET_TIME)
        self.last_warm_water_demand = self.now - QUIET_TIME
        self.outside_waiting_time = True
        self.outside_quiet_time= True
        self.outside_scheduled_run = True
        self.sanitycheck_failed = False

    def laeuft(self, pumpe_soll_laufen):
        """
        What shall the pump do?
        If it should run (True) than we check whether this request was given outside the waiting time,
        in which case the pump will start and we return true.
        If we the pump should not run (False), we check whether the running time has elaped
        and stop the pump in that case. We always return False.
        """
        if (pumpe_soll_laufen == True and \
                self.pumpe_laeuft == False):
            if self.outside_waiting_time: # Pump will only run outside wating_time
                self.pumpe_laeuft = True
                self.pumpenpin.off()
                info(f"{timetable.pt()}: Pump on")
                self.last_pumpenstart = self.now
            else:
                info(f"{timetable.pt()}: Request within waiting time. (pump stays 'off')")
            return True
        elif (pumpe_soll_laufen == False and \
              self.pumpe_laeuft == True and \
              (self.last_pumpenstart + RUNNING_TIME < self.now)):
            # Pump will run for RUNNING_TIME  s
            self.pumpe_laeuft = False
            self.pumpenpin.on()
            info(f"{timetable.pt()}: Pump off")
        return False # request False or trigger ignored

    def update_state(self):
        """
        Updates internal state variables
        """
        self.now = my_time()
        # Check sanity
        sanitycheck_failed = False 
        if self.last_pumpenstart > self.now:
            self.last_pumpenstart = self.now
            sanitycheck_failed = True
        if self.last_warm_water_demand > self.now:
            self.last_warm_water_demand = self.now
            sanitycheck_failed = True
        if self.last_scheduled_run > self.now:
            self.last_scheduled_run = self.now
            sanitycheck_failed = True
        if sanitycheck_failed:
            self.sanity_failed = my_time()

        # Set current status (waiting, quiet time, ...)
        if self.last_pumpenstart + WAITING_TIME < self.now:
            if not self.outside_waiting_time:
                info(f"{timetable.pt()}: Now outside waiting time")
            self.outside_waiting_time= True
            self.rgb_led.set(RGB_led.off)
        else:
            # indicate that pump can not be triggered in waiting time
            if self.outside_waiting_time:
                info(f"{timetable.pt()}: Now in waiting time")
            self.outside_waiting_time = False
            self.rgb_led.set(RGB_led.red) 

        if self.last_warm_water_demand + QUIET_TIME < self.now:
            if not self.outside_quiet_time:
                info(f"{timetable.pt()}: Now outside quiet time")
            self.outside_quiet_time= True
        else:
            if self.outside_quiet_time:
                info(f"{timetable.pt()}: Now in quite time")
            self.outside_quiet_time = False
            self.rgb_led.blink(RGB_led.red) # Indicate quiet time

        if self.last_warm_water_demand + HOLIDAY_TIME < self.now:
            # Last request for hot water more than 24h ago
            if not self.holiday: # Do not repeat info
                info(f"{timetable.pt()}: Entering holiday mode")
            self.holiday = True
            self.rgb_led.blink(RGB_led.yellow)
        else:
            if self.holiday:
                info(f"{timetable.pt()}: Leaving holiday mode")
            self.holiday = False

        if self.last_scheduled_run + QUIET_TIME < self.now:
            if not self.outside_scheduled_run:
                info(f"{timetable.pt()}: Outside scheduled run")
            self.outside_scheduled_run = True
        else:
            if self.outside_scheduled_run:
                info(f"{timetable.pt()}: Scheduled run")
            self.outside_scheduled_run = False

    def warm_water_demand(self):
        if self.temp.rising() \
            and self.outside_quiet_time \
            and self.outside_scheduled_run:
            # Real demand
            self.last_warm_water_demand = self.now
            return True
        return False

    def tick(self, args=None):
        """
        Periodic task
        läuft jede Sekunde
        """
        self.update_state()
        if self.warm_water_demand():
            info(f"{timetable.pt()}: Warm water request detected")
            # Request pump on
            # Pump stays off during waiting time!
            self.laeuft(True)
            self.ttable.check_item()  # Mark this in the timetable
        else:
            self.laeuft(False)                    # request pump off
        self.led_onboard.blink(ms=10) # Heartbeat (Should run at the end)

    def scheduled_run(self, args=None):
        """
        Starte pumpe gemäß timetable
        """
        self.last_scheduled_run = my_time() # Can not use self.now here
        self.update_state() # Needs valod self.last_scheduled_run
        if self.holiday:
            # If on holiday skip scheduled runs
            info(f"{timetable.pt()}: Holiday: skipping scheduled run")
            return
        info(f"{timetable.pt()}: Scheduled run")
        # Decrease the counter in the timetable
        slot_buffer = 2 # Security buffer (s) to ensure we are inside the right slot (not at the border)
        self.ttable.check_item(t=my_time() + QUIET_TIME + slot_buffer, increase=False)
        self.laeuft(True)

    def desinfect(self, args=None): # Start pump (every 72h) if no timetable exists
        """
        Start pump for desinfection during holiday and initialize next scheduled 
        run after e.g. timetable was empty
        """
        if (len(self.ttable.timetable) < 1 or self.holiday):
            # Treat this as a scheduled run
            self.last_scheduled_run = my_time()
            self.update_state()
            # No entry in timetable
            info(f"{timetable.pt()}: Desinfect run")
            self.laeuft(True) # Start pump
        self.led_onboard.blink(num=4)
        # Backup timetable
        info(f"{timetable.pt()}: Backup timetable")
        self.ttable.write_todisk()

class Backup():
    timestamp = 0
    def __init__(self, pumpe, stream):
        self.do_backup_ref = self.do_backup
        button = Pin(USR_PIN, Pin.IN)
        button.irq(trigger=Pin.IRQ_FALLING, handler=self._cb1)
        self.pumpe = pumpe
        self.stream = stream
    def _cb1(self, p = None):
        micropython.schedule(self.do_backup_ref, p)
    def do_backup(self, p = None):
        """
        Trigger a backup when USR Button is pressed
        """
        now = my_time()
        if now - self.timestamp < 2000:
            pass
            debug (f"{timetable.pt()}: Ignoring")
        else:
            info(f"{timetable.pt()}: Backup Button pressed: {p}")
            self.pumpe.rgb_led.blink(RGB_led.white)
            if self.pumpe.ttable.write_todisk():
                info(f"{timetable.pt()}: Timetable stored on disk")
                self.pumpe.rgb_led.blink(RGB_led.green)
            # Store stream
            if self.stream != sys.stdout:
                # Assume it is a StringIO
                msgs = stream.getvalue()
                if msgs:
                    with open(LOG_FILENAME,"a") as f:
                        o=f.write(msgs)
                        debug(f"{o} Bytes written to {LOG_FILENAME}")
                        self.pumpe.rgb_led.blink(RGB_led.green, num=2)
            self.timestamp = now
# Logger
stream = sys.stdout
#stream = io.StringIO()
ulogging.basicConfig(stream=stream) # INFO
#ulogging.basicConfig(level=ulogging.DEBUG,stream=stream)
pumpe=Pumpe()
# Prepare for backup via USR button
backup = Backup(pumpe, stream)
# Start processes
alarm_timer = Alarm_timer(pumpe)
