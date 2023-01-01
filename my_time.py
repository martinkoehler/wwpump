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
# Mockup for wokwi
import time
mock_time = 1672500136
if time.time() == 4451871603:
    from machine import Pin, I2C
    import ds1307
    # We assume a RTC is connected connected
    i2c=I2C(0,sda=Pin(0),scl=Pin(1))
    ds = ds1307.DS1307(i2c)
    def my_time():
        return time.mktime(ds.datetime())
else:
    def my_time():
        return time.time()