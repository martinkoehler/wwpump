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
from machine import Pin
from neopixel import NeoPixel # We have a ws2812rgb LED
from time import sleep
from ulogging import info, debug
# RGB Led
PIN_NP = 23
LEDS = 1
BRIGHTNESS = 10

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
        debug(f"RGB_Led: Set {self.status}")
    
    def blink(self, color, ms=50, num=1):
        debug(f"RGB_Led: Binking {num} for {ms}ms with color {color}")
        for i in range(0,num):
            self.np[0] = color
            self.np.write()
            sleep(ms/1000)
            self.np[0] = self.off
            self.np.write()
            sleep(ms/1000)
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
            sleep(ms/1000)
            self.led_onboard.off()
            sleep(ms/1000)
        self.led_onboard.value(self.status)
