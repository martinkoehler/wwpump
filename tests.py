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
#test
#ulogging.basicConfig(level=ulogging.DEBUG,stream=stream)
WAITING_TIME = 40 # Pump should only run every 30 seconds
RUNNING_TIME = 10 # Pump runs for 10 seconds
# quiet time 2 * running time
HOLIDAY_TIME = 80 # Holiday mode if no request for 60 seconds
DESINFECT_TIME = 120 # Run Desinfect every 2 minutes
pumpe=Pumpe()
ttable = timetable.Timetable()
# Prepare for backup via USR button
backup = Backup(pumpe, stream)
# Start processes
alarm_timer = Alarm_timer()
print("Testing")
# Test cases
time.sleep(3)
# rise temperature
print("Rise temperature")
Temp().t = [15.0] * 5
time.sleep(5)
# Must now have one timetable entry
print(f"Timetable with one entry {ttable.timetable}")
print(f"Next alert: {timetable.pt(my_time() + ttable.next_alarm())}")
print(f"...but no schedduled run (False): {alarm_timer.timer3_time}")
time.sleep(11)
print(f"Pump should be off now (False): {pumpe.pumpe_laeuft}")
# Inside quiet time!
print("Rising temp inside quiet time: No slot, no pumping")
Temp().t = [15.0] * 5
time.sleep(5)
print(f"Inside waiting time (10,0,0): {pumpe.rgb_led.status}")
print("Rising temp inside waiting time")
Temp().t = [15.0] * 5
# Must now have one timetable entry
print(f"{ttable.timetable} should have counter added")
print(f"Not in holiday mode (False): {pumpe.holiday}")
time.sleep(90)
print(f"Should be in holiday mode (True): {pumpe.holiday}")
print(f"No next scheduled run (False): {alarm_timer.timer3_time}")
print(f"Outside waiting time(0,0,0,0): {pumpe.rgb_led.status}")
print("Rising Temp")
Temp().t = [15.0] * 5
time.sleep(3)
print(f"Pumpe läuft (True): {pumpe.pumpe_laeuft}")
time.sleep(5)
print(f"Desinfect should have run")
print(f"Next scheduled run {timetable.pt(alarm_timer.timer3_time)}")
