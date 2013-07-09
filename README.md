hello
=====

A Google App Engine script to turn my hot tub on and off and record the
temperature. This is my first attempt at writing Go. It depends on an Electric
Imp which can adjust the temperature read by the IQ2020 by programmatically
putting an additional resistive load across the thermometer port. This resistor
is in parallel to real thermoresistor, so it lowers the voltage read by the
IQ2020 so it measures a higher then real temperature and it turns off the
heater. I used a 40k Ohm potentiometer adjusted to about 25k.


IQ2020
======

Some Python scripts which take i2c traffic captured by a Salae logic analyzer
and attempt to show what is happening at a higher level.

Example usage:

    sudo apt-get install python-pip python-pysqlite2
    sudo easy_install colorama
    python chomp.py --group_packets --print_packets --stateful_filter --hide_known samples/power-on-do-nothing.csv

