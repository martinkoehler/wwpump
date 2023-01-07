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
from time import sleep_ms
from ulogging import info, debug
import timetable
# GPIO-Pin für WS2812 RGB Led
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
    def __init__(self, leds=LEDS):
        self.status = []
        self.leds = leds
        self.np = NeoPixel(Pin(PIN_NP, Pin.OUT), leds)
        for i in range(leds):
            self.status.append(RGB_led.off)
            self.np[i] = self.status[i]
        self.np.write()

    def set(self,color, led = 0):
        """
        Set the color of one led (default index = 0)
        """
        if self.status[led] != color:
            info(f"{timetable.pt()}: RGB_Led: Set {led} to {color}")
        self.np[led] = color
        self.np.write()
        self.status[led] = color

    def blink(self, color, ms=50, num=1):
        """
        Blink all LEDs with color. Ensure that we set the LEDs off before and after and 
        restore their previous color at the end
        """
        debug(f"{timetable.pt()}: RGB_Led: Binking {num} for {ms}ms with color {color}")
        for i in range(num):
            # Set Leds off, but keep status
            for j in range(self.leds):
                self.status[j] = self.np[j]
                self.np[j] = self.off
            self.np.write()
            sleep_ms(ms)
            # Set color
            for j in range(self.leds):
                self.np[j] = color
            self.np.write()
            sleep_ms(ms)
            # Set Leds off again
            for j in range(self.leds):
                self.np[j] = self.off
            self.np.write()
            sleep_ms(ms)
            # Restore status
            for j in range(self.leds):
                self.np[j] = self.status[j]
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
        for i in range(num):
            self.led_onboard.on()
            sleep_ms(ms)
            self.led_onboard.off()
            sleep_ms(ms)
        self.led_onboard.value(self.status)
