# 
# This file is part of the wwwpump distribution
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
import micropython
import sys
import io
import time, onewire, ds18x20
from machine import Timer
from machine import Pin
from neopixel import NeoPixel # We have a ws2812rgb LED
import utime
import ulogging
from ulogging import info, debug

#  Define some constants
# RGB Led
PIN_NP = 23
LEDS = 1
BRIGHTNESS = 10

# DS18B20
DS18B20_PIN = 22
DS18B20_INDEX = 0 # Only one sensor

# Timetable
SLOT_TIME = 60 * 60 # (in sec) slots are 60 minutes
TIMETABLE_FILENAME = "timetable"

# Pumpe
PUMPEN_PIN = 20
WAITING_TIME = 60*60*1000 # Pump should only run every 60 minutes
QUIET_TIME = WAITING_TIME/20 # If we get a request after 3 minutes, increase the slot counter
RUNNING_TIME = 30*1000 # Pump runs for 30 seconds

# USR Button
USR_PIN = 13

# Backup
LOG_FILENAME = "wwpumpe.log"

# Helper functions
# ======================================
def pt(t = None):
    """
    Format a time integer in human readable form (pt for Pretty format Time)
    """
    if t == None:
        t = time.time()
    y, mm, d, h, m, s = time.localtime(t)[0:6]
    return f"{d:02d}.{mm:02d}.{y} {h:02d}:{m:02d}:{s:02d}"


# We use singletons
class Singleton(object):
  def __new__(cls):
    if not hasattr(cls, 'instance'):
      cls.instance = super(Singleton, cls).__new__(cls)
    return cls.instance

class RGB_led(Singleton):
    # GPIO-Pin für WS2812
    pin_np = PIN_NP
    # Anzahl der LEDs
    leds = LEDS
    # Helligkeit: 0 bis 255
    brightness = BRIGHTNESS
    white = (brightness, brightness, brightness)
    red = (brightness, 0, 0)
    green = (0, brightness, 0)
    blue = (0, 0, brightness)
    yellow = (brightness, brightness, 0)
    pink = (brightness, 0, brightness)
    turquoise = (0, brightness, brightness)
    off = (0, 0, 0)
    np = NeoPixel(Pin(pin_np, Pin.OUT), leds)
    def __init__(self):
        self.status = RGB_led.off
        self.np[0] = self.status
        self.np.write()
    
    def set(self,color):
        self.np[0] = color
        self.np.write()
        self.status = color
    
    def blink(self, color, ms=50, num=1):
        for i in range(0,num):
            self.np[0] = color
            self.np.write()
            utime.sleep_ms(ms)
            self.np[0] = self.off
            self.np.write()
            utime.sleep_ms(ms)
        self.np[0] = self.status
        self.np.write()
        
class Led(Singleton):
    def __init__(self):
        # Initialisierung von GPIO25 als Ausgang
        self.led_onboard = Pin(25, Pin.OUT)
        self.led_onboard.off()
        self.status = 0
    
    def on(self):
        self.led_onboard.on()
        self.status = 1
        
    def off(self):
        self.led_onboard.off()
        self.status = 0        
        
    def blink(self, ms=50, num=1):
        for i in range(0,num):
            self.led_onboard.on()
            utime.sleep_ms(ms)
            self.led_onboard.off()
            utime.sleep_ms(ms)
        self.led_onboard.value(self.status)

class Alarm_timer(Singleton):
    rgb_led = RGB_led()
    timer3 = Timer()
    def init_timers(self, pumpe):
        self.init_time = time.time()
        self.pumpe=pumpe
        self.pumpe_tick_ref=pumpe.tick
        self.pumpe_desinfect_ref=pumpe.desinfect
        self.pumpe_scheduled_run_ref = pumpe.scheduled_run
        self.timer1= Timer(period=1000, mode=Timer.PERIODIC, callback=self._cb1) # Worker
        self.timer2=Timer(period=3*24*60*60*1000, mode=Timer.PERIODIC, callback=self._cb2) # Alle 3 Tage
    
    def schedule_next_alarm(self, timetable):       
        alrm = timetable.next_alarm() # This is in seconds
        if alrm == False:
            return
        self.timer3.deinit() # Just to be on the safe side
        self.timer3 = Timer(period=alrm*1000, mode=Timer.ONE_SHOT, callback=self._cb3) # need ms here
        self.rgb_led.blink(RGB_led.green, num=2)
        self.timer3_time = time.time()+alrm # Store this in the class
        info(f"{pt()}: Next scheduled_run at: {pt(self.timer3_time)}")
     
    # For debugging
    def set_pumpe(pumpe):
        self.pumpe=pumpe

    def stop(self):
        self.timer1.deinit()
        self.timer2.deinit()
        self.timer3.deinit()

    # These call backs are interrupt driven, hence complicated functions are not allowed
    # We use micropython.schedule to start the "real" worker
    # We war not allowed to allocate memory in the ISR See
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
        ow = onewire.OneWire(Pin(DS18B20_PIN)) # create a OneWire bus on GPIO22
        self.ds = ds18x20.DS18X20(ow)
        self.rom = self.ds.scan()[DS18B20_INDEX] # Only one sensor
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
        cnt_alt  = (self.cnt + 4) % 5 # Previous element in the buffer
        temperatur_delta = self.t[self.cnt]-self.t[cnt_alt]; # Temperaturdifferenz der letzten
                                                             # 5 Messzyklen bzw. Sekunden
        if (temperatur_delta >= 0.12): # 0.12°
            debug(f"{pt()}: Rising temperature: {temperature}")
            self.led_onboard.on()
            return True
        self.led_onboard.off()
        return False
                   

    def _get_temperature(self):
        self.ds.convert_temp()
        # Warten: min. 750 ms
        utime.sleep_ms(750)
        return self.ds.read_temp(self.rom)



class Timetable(Singleton):
    """
    Implements a timetable to store the slots where we turn the pump on
    """
    # timetable is an array that stores tuples [wday,hour,min,cnt], where cnt is a counter,
    # which ensures that entries are deleted if not used
    timetable = [] # Leere Tabelle
    slot_time = SLOT_TIME
    rgb_led = RGB_led()
    alarm_timer = Alarm_timer()
    
    def __init__(self):
        self.read_fromdisk() # If we have a timetable on disk, read it
        self.alarm_timer.schedule_next_alarm(self) # Make sure that we initialize after reading the data from disk

    def check_item(self, t = None, increase = True):
        """
        If we get a new item, we search whether this falls in an already existing slot
        in this case increment the counter in the timetable
        If not add the item as new slot
        """
        if t == None:
            t = time.time()
        index = self._in_timetable(t)
        if index == None:
            # Element new!
            # Subtract half slot_time
            t -= int(self.slot_time/2) # a bit earlier
            # Check whether we are now in a slot
            index = self._in_timetable(t)
            if index == None:
                # Still not in a slot
                # Add this slot
                self._add_slot(t)
                self.rgb_led.blink(RGB_led.blue)
                return
        # Already in the table or no new slot -> handle counter (wday,h,m,s,cnt]
        if increase:
            self.timetable[index][4] += 1
            debug(f"{pt()}: Slot found. Counter increased {self._format_slot(self.timetable[index])}")
            self.rgb_led.blink(RGB_led.yellow)
        else:
            self.timetable[index][4] -= 1
            debug(f"{pt()}: Slot found. Counter decreased {self._format_slot(self.timetable[index])}")
            if self.timetable[index][4] < 1:
                self.timetable.pop(index)
                debug("Entry removed")
                self.rgb_led.blink(RGB_led.blue, num=2)
                
            
    def next_alarm(self,t = None):
        """
        Returns next alarm time in ms
        or False if no entry in the timetable
        """
        if t == None:
            t = time.time()
        week = 7 * 24 * 60 * 60 # One week in secons
        if len(self.timetable) < 1:
            return False
        index = self._next_slot(t)
        slot_wd, slot_h, slot_m, slot_s = self.timetable[index][0:-1] # We do not need the counter here
        slot_base_time = self._to_base_time([slot_h, slot_m, slot_s, slot_wd])
        base_time = self._to_base_time(time.localtime(t)[3:7])
        if slot_base_time <= base_time:
            return slot_base_time + week - base_time
        else:
            return slot_base_time - base_time
       
    def write_todisk(self, name=TIMETABLE_FILENAME):
        """
        store the timetable on disk
        """
        if len(self.timetable) < 1:
            debug("No data in timetable to write")
            return False
        with open(name, "w") as f:
            o=f.write(str(self.timetable))
            debug(f"{o} Bytes written to {name}")
            return True
            
    def read_fromdisk(self, name=TIMETABLE_FILENAME):
        """
        Reads a timetable from disk and initializes the local variable
        """
        try:
            with open(name,"r") as f:
                o = t_table = f.read()
                debug(f"{o} Bytes read from {name}")
            self.timetable = eval(t_table)
            info(f"{len(self.timetable)} entries read from {name}")
        except OSError:
            debug(f"{pt(time.time())}: No file {name} found.")
            return False
        return True
        
    def _add_slot(self, t):
        """
        Add an item to the timetable and sort the table
        """
        h, m, s, wd = time.localtime(t)[3:7]
        cnt = 1 # Remove after one week
        slot = [wd,h,m,s,cnt]
        info(f"{pt()}: Adding Slot {self._format_slot(slot)}")
        self.timetable.append(slot)
        # Sort the table using all entries 0 padded
        self.timetable.sort(key=lambda elem: "".join([f"{i:02}" for i in elem]))
        if len(self.timetable) < 2:
            # Now only one  entry ? -> Need to schedule alarm
            # We need a properly initialized Alarm_timer class here, e.g.
            # pumpe must be defined
            try:
                pumpe = self.alarm_timer.pumpe
                self.alarm_timer.schedule_next_alarm(self)
            except AttributeError:
                debug(f"{pt()}: Can not reschedule alarm without a 'pumpe' object")
    
    def _format_slot(self, slot):
        """
        Human readable form of a slot
        """
        days = ["Mon","Tue","Wed","Thu","Fri","Sat","Sun"]
        return f"'{days[slot[0]]}: {slot[1]:02}:{slot[2]:02}:{slot[3]:02} Counter:{slot[4]}'"
            
    def _to_base_time(self, lst):
        """
        converts a list [h,m,s,wd] to base in 1970 to ensure that we can substract
        """
        h, m, s, wd = lst
        base_date = (1970, 1, 5 + wd, h, m, s, 0, 0) #  (1970, 1, 5, 0, 0, 0, 0, 5)
        base_time = time.mktime(base_date)
        return base_time
        
        
    def _next_slot(self, t):
        """
        Find the next slot for a given time t
        We assume the timetable is sorted!
        returns index or false if timetable empty
        """
        h, m, s, wd = time.localtime(t)[3:7]
        slot_str = "".join(f"{i:02}" for i in [wd,h,m,s]) # The last 0 is a dummy
        # Create auxiliary table just containing zero padded strings form the first 4 slot elements,
        # e.g. without the counter
        timetable_str = [ "".join([f"{i:02}" for i in elem[0:-1]]) for elem in self.timetable]
        for i in range(0, len(timetable_str)):
            if timetable_str[i] < slot_str:
                continue
            return i
        # If there are entries in the timetable, wrap around
        if len(timetable_str) > 0:
            return 0 # First element
        return False
    
 
    def _in_timetable(self, t):
        """
        Check wether time is in the timetable
        returns timetable index or None
        """
        local_base_time = self._to_base_time(time.localtime(t)[3:7])
        for i in range(0,len(self.timetable)):
            wday, hour, min, sec = self.timetable[i][0:-1]
            base_time = self._to_base_time([hour,min,sec,wday]) 
            max_time = base_time + self.slot_time
            min_time = base_time
            if min_time <= local_base_time <= max_time:
                return i
        return None


 
class Pumpe(Singleton):
    timer_pumpenlaufzeit = 0
    timer_pause = 0
    pumpe_laeuft = False
    temp = Temp()
    rgb_led = RGB_led()
    timetable = Timetable()
    led_onboard = Led()
    alarm_timer = Alarm_timer()
    
    def __init__(self):
        self.pumpenpin = Pin(PUMPEN_PIN, Pin.OUT)
        self.pumpenpin.on() # Low -> Pumpe ein
        self.pumpe_laeuft = False
        self.rgb_led.set(RGB_led.off)
        self.timer_pumpenlaufzeit = 0 # timer_pumpenlaufzeit: running time of the pump
        self.timer_pause = 0 # timer_pause: gap between successive pump runs
        self.timer_quiet = 0 # timer_quiet: ignore request in quiet time 

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
        waiting_time = WAITING_TIME # Pump will only run again after WAITING_TIME seconds
        quiet_time = QUIET_TIME     # a request will be ignored if it occurs within the quiet_time
        running_time = RUNNING_TIME # Pump runs for RUNNING_TIME seconds
        now_ms = time.ticks_ms()
        if (now_ms - self.timer_pause) > waiting_time:
            self.rgb_led.set(RGB_led.off)
        else:
            self.rgb_led.set(RGB_led.red) # indicate that pump can not be triggered in waiting time
        if (pumpe_soll_laufen == True and \
                self.pumpe_laeuft == False):
            if ((now_ms - self.timer_pause) > waiting_time or
                 self.timer_pause > now_ms): # Pump will only run outside wating_time (30min) 
                self.pumpe_laeuft = True
                self.pumpenpin.off()
                info(f"{pt()}: Pump on")
                self.timer_pumpenlaufzeit = self.timer_pause = self.timer_quiet = now_ms
                return True
            elif ((now_ms - self.timer_quiet) > quiet_time):
                self.timer_quiet = now_ms
                return True # indicates that we are inside waiting time but outside repeated buffer
        elif (pumpe_soll_laufen == False and \
              self.pumpe_laeuft == True and \
              (now_ms - self.timer_pumpenlaufzeit) > running_time): # Pump will run for running_time (30s)
            self.pumpe_laeuft = False
            self.pumpenpin.on()
            info(f"{pt()}: Pump off")
        return False # request False or trigger ignored

    def tick(self, args=None):
        """
        Periodic task
        läuft jede Sekunde
        """
        self.led_onboard.blink(ms=10) # Heartbeat
        if self.temp.rising():
            if (self.laeuft(True) == True):       # if within 5s the temp > = 0.12°C, request pump on
                self.timetable.check_item()       # Mark this in the timetable, except when within 1/6th of waiting time
        else:
            self.laeuft(False)                    # request pump off
    
    def scheduled_run(self, args=None):
        """
        Starte pumpe gemäß timetable
        """
        # Decrease the counter in the timetable
        self.timetable.check_item(t=time.time() + 10, increase=False) # Add 10s to make sure
                                                                      # we are in the slot
        self.laeuft(True)
        self.alarm_timer.schedule_next_alarm(self.timetable)     
   
    def desinfect(self, args=None): # Start pump (every 72h) if no timetable exists
        if (len(self.timetable.timetable) < 1):
            # No entry in timetable
            self.laeuft(True) # Start pump
        self.led_onboard.blink(num=2)
        # Backup timetable
        self.timetable.write_todisk()

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
            #debug (f"{pt()}: Ignoring")
        else:
            debug(f"{pt()}: Button pressed: {p}")
            RGB_led().blink(RGB_led.white)
            if self.pumpe.timetable.write_todisk():
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
            


# Logger
stream = sys.stdout
#stream = io.StringIO()
ulogging.basicConfig(level=ulogging.DEBUG,stream=stream)
#ulogging.basicConfig(stream=stream) # INFO

pumpe = Pumpe()
# Prepare for backup via USR button
backup = Backup(pumpe, stream)
# Start processes
pumpe.alarm_timer.init_timers(pumpe)