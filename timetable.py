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

# Timetable
SLOT_TIME = 15 # (in min) slots are 15 minutes (Slots must divide the hour!)
TIMETABLE_FILENAME = "timetable"

from ulogging import info, debug
import time
from my_time import my_time

# Helper functions
# ======================================
def pt(t = None):
    """
    Format a time integer in human readable form (pt for Pretty format Time)
    """
    if t == None:
        t = my_time()
    y, mm, d, h, m, s = time.localtime(t)[0:6]
    return f"{d:02d}.{mm:02d}.{y} {h:02d}:{m:02d}:{s:02d}"

class Timetable():
    """
    Implements a timetable to store the slots where we turn the pump on
    """
    # timetable is an array that stores tuples [wday,hour,min,cnt], where cnt is a counter,
    # which ensures that entries are deleted if not used
    timetable = [] # Leere Tabelle
    slot_time = SLOT_TIME

    def __init__(self):
        self.read_fromdisk() # If we have a timetable on disk, read it

    def check_item(self, t = None, increase = True):
        """
        If we get a new item, we search whether this falls in an already existing slot
        in this case increment the counter in the timetable
        If not add the item as new slot
        """
        if t == None:
            t = my_time()
        index = self._in_timetable(t)
        if index == None:
            self._add_slot(t)
            return
        # Already in the table or no new slot -> handle counter (wday,h,m,s,cnt]
        if increase:
            self.timetable[index][4] += 1
            info(f"{pt()}: Slot found. Counter increased {self._format_slot(self.timetable[index])}")
        else:
            self.timetable[index][4] -= 1
            info(f"{pt()}: Slot found. Counter decreased {self._format_slot(self.timetable[index])}")
            if self.timetable[index][4] < 1:
                self.timetable.pop(index)
                info("Entry removed")
            
    def next_alarm(self,t = None):
        """
        Returns next alarm time in s from t (or my_time() == now)
        or False if no entry in the timetable
        """
        if t == None:
            t = my_time()
        week = 7 * 24 * 60 * 60 # One week in seconds
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
            info("No data in timetable to write")
            return False
        with open(name, "w") as f:
            o=f.write(str(self.timetable))
            info(f"{o} Bytes written to {name}")
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
            info(f"{pt(my_time())}: No file {name} found.")
            return False
        return True
        
    def _add_slot(self, t):
        """
        Add an item to the timetable and sort the table
        Return True in case less than two entries remain 
        """
        h, m, s, wd = time.localtime(t)[3:7]
        # Force Slots
        slot_m = m // self.slot_time * self.slot_time
        m = slot_m
        s = 0
        cnt = 1 # Remove after one week
        slot = [wd,h,m,s,cnt]
        info(f"{pt()}: Adding Slot {self._format_slot(slot)}")
        self.timetable.append(slot)
        # Sort the table using all entries 0 padded
        self.timetable.sort(key=lambda elem: "".join([f"{i:02}" for i in elem]))
        if len(self.timetable) < 2:
            # Probably need to schedule next alarm 
            return True
        return False
        
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
        base_date = (1970, 1, 5 + wd, h, m, s, 0, 0, 0) #  (1970, 1, 5, 0, 0, 0, 0, 0, 0)
        #print(base_date)
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
            max_time = base_time + self.slot_time * 60
            min_time = base_time
            if min_time <= local_base_time <= max_time:
                return i
        return None