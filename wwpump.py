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
from my_time import my_time


# DS18B20
DS18B20_PIN = 22
DS18B20_INDEX = 0 # Only one sensor

# Pumpe
PUMPEN_PIN = 20

# All thess times are in s
WAITING_TIME = 15*60 # Pump should only run every 15 minutes
RUNNING_TIME = 40 # Pump runs for 40 seconds

# USR Button
USR_PIN = 13

# Backup
LOG_FILENAME = "wwpumpe.log"

class Alarm_timer(Singleton):
    timer3 = Timer()
    def __init__(self):
        self.pumpe=Pumpe()
        self.ttable = timetable.Timetable() 
        self.pumpe_tick_ref=pumpe.tick
        self.pumpe_desinfect_ref=pumpe.desinfect
        self.pumpe_scheduled_run_ref = self.pumpe_scheduled_run
        self.timer1= Timer(period=1000, mode=Timer.PERIODIC, callback=self._cb1) # Worker
        self.timer2=Timer(period=3*24*60*60*1000, mode=Timer.PERIODIC, callback=self._cb2) # Alle 3 Tage
    
    def schedule_next_alarm(self, timetable):       
        alrm = timetable.next_alarm() # This is in seconds
        self.timer3.deinit() # Just to be on the safe side
        if alrm == False:
            info("No next alarm scheduled")
            return
        # Start one minute earlier
        if alrm > 60:
            # Ensure that alrm remains > 0
            alrm -= 60
        self.timer3 = Timer(period=alrm*1000, mode=Timer.ONE_SHOT, callback=self._cb3) # need ms here
        self.timer3_time = my_time()+alrm # Store this in the class
        info(f"{timetable.pt()}: Next scheduled_run at: {timetable.pt(self.timer3_time)}")
    
    def pumpe_scheduled_run(self):
        self.pumpe.scheduled_run()
        self.schedule_next_alarm(self.ttable)   
     
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
            info("No DS18B20 sensor found. Will use mock up")
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



 
class Pumpe(Singleton):
    holiday = False
    pumpe_laeuft = False
    def __init__(self):
        self.temp = Temp()
        self.rgb_led = RGB_led()
        self.ttable = timetable.Timetable()
        self.led_onboard = Led()
        self.pumpenpin = Pin(PUMPEN_PIN, Pin.OUT)
        self.pumpenpin.on() # Low -> Pumpe ein
        self.timer_lastpumpenstart = - WAITING_TIME * 1000
        self.rgb_led.set(RGB_led.off)
        self.last_scheduled_run_ms = time.ticks_ms()
        self.last_temp_rising = time.ticks_ms()

    def laeuft(self, pumpe_soll_laufen):
        """
        What shall the pump do?
        If it should run (True) than we check whether this request was given outside the waiting time, in which case
        the pump will start and we return true.
        If we are within the first 1/6 th of the waiting time, we return false
        If within the waiting time but outside first 1/6, we do not start the pump, but return true
        If we the pump should not run (False), we check whether the running time has elaped and stop the pump
        in that case. We always return False.
        """
        waiting_time_ms = WAITING_TIME * 1000   # Pump will only run again after WAITING_TIME seconds
        quiet_time_ms = 2 * RUNNING_TIME * 1000 # a request will be ignored if it occurs within the quiet_time
        running_time_ms = RUNNING_TIME * 1000   # Pump runs for RUNNING_TIME seconds
        now_ms = time.ticks_ms()
        if self.timer_lastpumpenstart + waiting_time_ms < now_ms:
            self.rgb_led.set(RGB_led.off)
            outside_waiting_time= True
        else:
            self.rgb_led.set(RGB_led.red) # indicate that pump can not be triggered in waiting time
            outside_waiting_time = False
        if (pumpe_soll_laufen == True and \
                self.pumpe_laeuft == False):
            if outside_waiting_time: # Pump will only run outside wating_time 
                self.pumpe_laeuft = True
                self.pumpenpin.off()
                info(f"{timetable.pt()}: Pump on")
                self.timer_lastpumpenstart = now_ms
                return True
            elif self.timer_lastpumpenstart + quiet_time_ms < now_ms:
                # real warm water request within waiting time
                # i.e. inside wating time, but after quiet_time
                # return True to indicate that timetable sould be updated
                self.timer_lastpumpenstart = now_ms
                return True
        elif (pumpe_soll_laufen == False and \
              self.pumpe_laeuft == True and \
              (self.timer_lastpumpenstart + running_time_ms < now_ms)):
            # Pump will run for running_time_ms (30s)
            self.pumpe_laeuft = False
            self.pumpenpin.on()
            info(f"{timetable.pt()}: Pump off")
        return False # request False or trigger ignored

    def tick(self, args=None):
        """
        Periodic task
        läuft jede Sekunde
        """
        self.led_onboard.blink(ms=10) # Heartbeat
        now_ms = time.ticks_ms()
        if self.temp.rising():
            # Is the reason a scheduled run?
            if self.last_scheduled_run_ms + 2 * RUNNING_TIME * 1000 < now_ms:
                # Real demand
                self.holiday = False
                self.rgb_led.set(RGB_led.off)
                self.last_temp_rising = now_ms
            elif (self.last_temp_rising + 24 * 60 * 60 * 1000 < now_ms):
                # Last request for hot water more than 24h ago
                self.holiday = True
                self.rgb_led.set(RGB_led.yellow)
            if (self.laeuft(True) == True):       # if within 5s the temp > = 0.12°C, request pump on
                self.ttable.check_item()       # Mark this in the timetable, except when within 1/6th of waiting time
        else:
            self.laeuft(False)                    # request pump off
    
    def scheduled_run(self, args=None):
        """
        Starte pumpe gemäß timetable
        """
        self.last_scheduled_run_ms = time.ticks_ms()
        if self.holiday:
            # If on holiday skip scheduled runs
            return
        # Decrease the counter in the timetable
        self.ttable.check_item(t=my_time(), increase=False)
        self.laeuft(True)
         
   
    def desinfect(self, args=None): # Start pump (every 72h) if no timetable exists
        if (len(self.ttable.timetable) < 1 or self.holiday):
            # Treat this as a scheduled run
            self.last_scheduled_run_ms = time.ticks_ms()
            # No entry in timetable
            self.laeuft(True) # Start pump
        self.led_onboard.blink(num=4)
        # Backup timetable
        self.ttable.write_todisk()

class Backup():
    timestamp_ms = 0
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
        now_ms = time.ticks_ms()
        if now_ms - self.timestamp_ms < 2000:
            pass
            #debug (f"{timetable.pt()}: Ignoring")
        else:
            info(f"{timetable.pt()}: Backup Button pressed: {p}")
            RGB_led().blink(RGB_led.white)
            if self.pumpe.ttable.write_todisk():
                info("Timetable stored on disk")
                RGB_led().blink(RGB_led.green)
            # TO DO Store stream
            if self.stream != sys.stdout:
                # Assume it is a StringIO
                msgs = stream.getvalue()
                if msgs:
                    with open(LOG_FILENAME,"a") as f:
                        o=f.write(msgs)
                        debug(f"{o} Bytes written to {LOG_FILENAME}")
                        RGB_led().blink(RGB_led.green, num=2)
            self.timestamp_ms = now_ms
            
# TO DO
# Handle holidays -> If no temp_rising within 24h -> holiday modus -> do not schedule next alarm

# Logger
stream = sys.stdout
#stream = io.StringIO()
#ulogging.basicConfig(level=ulogging.DEBUG,stream=stream)
ulogging.basicConfig(stream=stream) # INFO

pumpe=Pumpe()
ttable = timetable.Timetable()

# Prepare for backup via USR button
backup = Backup(pumpe, stream)
# Start processes
alarm_timer = Alarm_timer()
