# fan_test.py
import time

import RPi.GPIO as GPIO

# --- Configuration ---
# The GPIO pin number your fan's control wire is connected to.
# This uses BCM numbering, which refers to the Broadcom chip's pin numbers.
FAN_PIN = 16

# Set the pin numbering scheme to BCM.
GPIO.setmode(GPIO.BCM)
# Configure the specified fan pin as an output pin.
GPIO.setup(FAN_PIN, GPIO.OUT)

try:
    # This is the main logic block.
    print("Turning fan ON for 15 seconds...")
    # Set the pin to HIGH, which typically provides voltage and turns the fan on.
    GPIO.output(FAN_PIN, GPIO.HIGH)
    # Pause the script for 15 seconds while the fan is running.
    time.sleep(15)

    print("Turning fan OFF.")
    # Set the pin to LOW, which cuts the voltage and turns the fan off.
    GPIO.output(FAN_PIN, GPIO.LOW)

finally:
    # This block is crucial. It will run no matter what, even if the script
    # is interrupted (e.g., with Ctrl+C).
    print("Cleaning up GPIO pins.")
    # GPIO.cleanup() resets all the GPIO pins you've used back to their default
    # state, preventing potential issues on the next run.
    GPIO.cleanup()
