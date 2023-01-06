# wwpump
Application to drive a warm water circulation pump with a Pico Board RP2040

This follows the ideas from https://www.heise.de/select/make/2022/4/2216608454200440892, but uses a Pico Pi board.

For more information please refer to the Make article or https://github.com/MakeMagazinDE/Zirkulationspumpensteuerung

For a simulation see https://wokwi.com/projects/353105419471095809

As material I used:
* DS18B20 temperatur sensor (in plastic case)
* Resistor 4,7k Ohm for the sensor
* RP2040 board (USB-C variant with RGB LED and USR switch RP2040 Dual-Core 264KB ARM Low-Power Mikrocomputern)
* Low Level 5V 1 Kanal SSR G3MB-202P Solid State Relais Modul 240V 2A output with for arduino
* LM2596s DC-DC step down power supply module 3A, since I had 12V available (You can also use the USB-C for Power)

Main differences are:
* I implemented a mechanism that remembers whenever warm water was needed and adds a corresponding slot, so that in a week 
the pump starts some minutes earlier automatically in order to provide warm water. 
If a slot is not used for some time it gets deleted automatically.
* The desinfection logic is not implemented, since I observered that in reality there is always a slot active that starts the pump.

I furthermore used an electronic relais connected to PIN 20 to drive the pump. The VCC of the relais is conneced to VBUS, since it needs 5V to work.
The DS18B20 is connected to pin 22 and a 4,7kOhm resistor connects the bus signal to 3.3V out of the RP2040.
