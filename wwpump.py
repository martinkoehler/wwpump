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
SLOT_TIME = 15 * 60 # (in sec) slots are 30 minutes
TIMETABLE_FILENAME = "timetable"

# Pumpe
PUMPEN_PIN = 20
WAITING_TIME = 30*60*1000 # 30*60*1000 # Pumpe soll nur alle 30 Minuten laufen
RUNNING_TIME = 30*1000 # Pumpe laeuft für 30 Sekunden

# USR Button
USR_PIN = 13

# Backup
LOG_FILENAME = "wwpumpe.log"

# Helper functions
# ======================================
def pt(t = time.time()):
    """
    Format a time integer in human readable form (pt for Pretty format Time)
    """
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
        self.pumpe=pumpe
        self.timer1= Timer(period=1000, mode=Timer.PERIODIC, callback=self._cb1) # Worker
        self.timer2=Timer(period=3*24*60*60*1000, mode=Timer.PERIODIC, callback=self._cb2) # Alle 3 Tage
    
    def schedule_next_alarm(self, timetable):       
        alrm = timetable.next_alarm() # This is in seconds
        if alrm == False:
            return
        self.timer3.deinit() # Just to be on the safe side
        self.timer3 = Timer(period=alrm*1000, mode=Timer.ONE_SHOT, callback=self._cb3) # need ms here
        self.rgb_led.blink(RGB_led.green, num=2)
        info(f"{pt()}: Next scheduled_run at: {pt(time.time()+alrm)}")
    
    # For debugging
    def set_pumpe(pumpe):
        self.pumpe=pumpe

    def stop(self):
        self.timer1.deinit()
        self.timer2.deinit()
        self.timer3.deinit()

    # These call backs are interrupt driven, hence complicated functions are not allowed
    # We use micropython.schedule to start the "real" worker
    def _cb1(self, tim):
        micropython.schedule(self.pumpe.tick, tim)
    def _cb2(self, tim):
        micropython.schedule(self.pumpe.desinfect, tim)
    def _cb3(self, tim):
        micropython.schedule(self.pumpe.scheduled_run, tim)
    

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

    def check_item(self, t=time.time(), increase = True):
        """
        If we get a new item, we search whether this falls in an already existing slot
        in this case increment the counter in the timetable
        If not add the item as new slot
        """
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
                
            
    def next_alarm(self,t=time.time()):
        """
        Returns next alarm time in ms
        or False if no entry in the timetable
        """
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
        return
        with open(name, "w") as f:
            f.write(str(self.timetable))
            
    def read_fromdisk(self, name=TIMETABLE_FILENAME):
        """
        Reads a timetable from disk and initializes the local variable
        """
        try:
            with open(name,"r") as f:
                t_table = f.read()
            self.timetable = eval(t_table)
        except OSError:
            debug(f"{pt(time.time())}: No file {name} found.")
                
        
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
    
    def _format_slot(self,slot):
        """
        Human readable form of a slot
        """
        days = ["Mon","Tue","Wed","Thu","Fri","Sat","Sun"]
        return f"'{days[slot[0]]}: {slot[1]:02}:{slot[2]:02}:{slot[3]:02} Counter:{slot[4]}'"
            
    def _to_base_time(self,lst):
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
        self.timer_pumpenlaufzeit = 0 # timer_pumpenlaufzeit: steuert Laufzeit der Pumpe
        self.timer_pause = 0 # timer_pause steuert die Größe der Pause zwischen den Laufzeiten

    def laeuft(self, pumpe_soll_laufen):
        waiting_time = WAITING_TIME # Pumpe will only run again after WAITING_TIME seconds
        running_time = RUNNING_TIME # Pumpe runs for RUNNING_TIME seconds
        if (time.ticks_ms() - self.timer_pause) > waiting_time:
            self.rgb_led.set(RGB_led.off)
        else:
            self.rgb_led.set(RGB_led.red) # indicate that pump can not be triggered in waiting time
        if (pumpe_soll_laufen == True and \
            self.pumpe_laeuft == False and \
            (time.ticks_ms() - self.timer_pause) > waiting_time ): # Pump will only run outside wating_time (30min) 
            self.pumpe_laeuft = True                               
            self.pumpenpin.off()
            info(f"{pt()}: Pump on")
            self.timer_pumpenlaufzeit = time.ticks_ms()
            self.timer_pause = time.ticks_ms()
            return True
        if (pumpe_soll_laufen == False and \
            self.pumpe_laeuft == True and \
            (time.ticks_ms() - self.timer_pumpenlaufzeit) > running_time): # Pump will run for running_time (30s)
            self.pumpe_laeuft = False
            self.pumpenpin.on()
            info(f"{pt()}: Pump off")
        return False 

    def tick(self, args=None):
        """
        Periodic task
        läuft jede Sekunde
        """
        self.led_onboard.blink(ms=10) # Heartbeat
        if self.temp.rising():
            if (self.laeuft(True) == True):       # wenn binnen 5 Sekunden > = 0.12°C gestiegen ist, Pumpe an!
                self.timetable.check_item()
        else:
            self.laeuft(False)
    
    def scheduled_run(self, args=None):
        """
        Starte pumpe gemäß timetable
        """
        # Decrease the counter in the timetable
        self.timetable.check_item(increase=False)
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
    timestamp = 0.0

    def __init__(self, pumpe, stream):
        #self.timestamp = utime.ticks_ms()
        button = Pin(USR_PIN, Pin.IN)
        button.irq(trigger=Pin.IRQ_FALLING, handler=self._cb1)
        self.pumpe = pumpe
        self.stream = stream
        
    def _cb1(self, p):
        micropython.schedule(self.do_backup, p)
        
    def do_backup(self, p = None):
        """
        Trigger a backup when USR Button is pressed
        """
        now = utime.ticks_ms()
        if now - self.timestamp < 2000:
            pass
            #debug (f"{pt()}: Ignoring")
        else:
            debug(f"{pt()}: Button pressed: {p}")
            RGB_led().blink(RGB_led.white)
            self.pumpe.timetable.write_todisk()
            # TO DO Store stream
            if self.stream != sys.stdout:
                # Assume it is a StringIO
                msgs = stream.getvalue()
                if msgs:
                    with open(LOG_FILENAME,"a") as f:
                        f.write(msgs)
            self.timestamp = now
            RGB_led().blink(RGB_led.white, num=2)


# Logger
stream = sys.stdout
stream = io.StringIO()
#ulogging.basicConfig(level=ulogging.DEBUG,stream=sys.stdout)
ulogging.basicConfig(stream=stream) # INFO

pumpe = Pumpe()
# Prepare for backup via USR button
backup = Backup(pumpe, stream)
# Start processes
pumpe.alarm_timer.init_timers(pumpe)