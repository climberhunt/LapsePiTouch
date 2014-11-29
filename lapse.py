# Lapse-Pi timelapse controller for Raspberry Pi
# This must run as root (sudo python lapse.py) due to framebuffer, etc.
#
# http://www.adafruit.com/products/998  (Raspberry Pi Model B)
# http://www.adafruit.com/products/1601 (PiTFT Mini Kit)
#
# Prerequisite tutorials: aside from the basic Raspbian setup and PiTFT setup
# http://learn.adafruit.com/adafruit-pitft-28-inch-resistive-touchscreen-display-raspberry-pi
#
# lapse.py by David Hunt (dave@davidhunt.ie)
# based on cam.py by Phil Burgess / Paint Your Dragon for Adafruit Industries.
# BSD license, all text above must be included in any redistribution.

import wiringpi2
import atexit
import cPickle as pickle
import errno
import fnmatch
import io
import os
import pygame
import threading
import signal
import sys

from pygame.locals import *
from subprocess import call  
from time import sleep
from datetime import datetime, timedelta

# UI classes ---------------------------------------------------------------

# Icon is a very simple bitmap class, just associates a name and a pygame
# image (PNG loaded from icons directory) for each.
# There isn't a globally-declared fixed list of Icons.  Instead, the list
# is populated at runtime from the contents of the 'icons' directory.

class Icon:

	def __init__(self, name):
	  self.name = name
	  try:
	    self.bitmap = pygame.image.load(iconPath + '/' + name + '.png')
	  except:
	    pass

# Button is a simple tappable screen region.  Each has:
#  - bounding rect ((X,Y,W,H) in pixels)
#  - optional background color and/or Icon (or None), always centered
#  - optional foreground Icon, always centered
#  - optional single callback function
#  - optional single value passed to callback
# Occasionally Buttons are used as a convenience for positioning Icons
# but the taps are ignored.  Stacking order is important; when Buttons
# overlap, lowest/first Button in list takes precedence when processing
# input, and highest/last Button is drawn atop prior Button(s).  This is
# used, for example, to center an Icon by creating a passive Button the
# width of the full screen, but with other buttons left or right that
# may take input precedence (e.g. the Effect labels & buttons).
# After Icons are loaded at runtime, a pass is made through the global
# buttons[] list to assign the Icon objects (from names) to each Button.

class Button:

	def __init__(self, rect, **kwargs):
	  self.rect     = rect # Bounds
	  self.color    = None # Background fill color, if any
	  self.iconBg   = None # Background Icon (atop color fill)
	  self.iconFg   = None # Foreground Icon (atop background)
	  self.bg       = None # Background Icon name
	  self.fg       = None # Foreground Icon name
	  self.callback = None # Callback function
	  self.value    = None # Value passed to callback
	  for key, value in kwargs.iteritems():
	    if   key == 'color': self.color    = value
	    elif key == 'bg'   : self.bg       = value
	    elif key == 'fg'   : self.fg       = value
	    elif key == 'cb'   : self.callback = value
	    elif key == 'value': self.value    = value

	def selected(self, pos):
	  x1 = self.rect[0]
	  y1 = self.rect[1]
	  x2 = x1 + self.rect[2] - 1
	  y2 = y1 + self.rect[3] - 1
	  if ((pos[0] >= x1) and (pos[0] <= x2) and
	      (pos[1] >= y1) and (pos[1] <= y2)):
	    if self.callback:
	      if self.value is None: self.callback()
	      else:                  self.callback(self.value)
	    return True
	  return False

	def draw(self, screen):
	  if self.color:
	    screen.fill(self.color, self.rect)
	  if self.iconBg:
	    screen.blit(self.iconBg.bitmap,
	      (self.rect[0]+(self.rect[2]-self.iconBg.bitmap.get_width())/2,
	       self.rect[1]+(self.rect[3]-self.iconBg.bitmap.get_height())/2))
	  if self.iconFg:
	    screen.blit(self.iconFg.bitmap,
	      (self.rect[0]+(self.rect[2]-self.iconFg.bitmap.get_width())/2,
	       self.rect[1]+(self.rect[3]-self.iconFg.bitmap.get_height())/2))

	def setBg(self, name):
	  if name is None:
	    self.iconBg = None
	  else:
	    for i in icons:
	      if name == i.name:
	        self.iconBg = i
	        break

# UI callbacks -------------------------------------------------------------
# These are defined before globals because they're referenced by items in
# the global buttons[] list.

def motorCallback(n): # Pass 1 (next setting) or -1 (prev setting)
	global screenMode
	global motorRunning
	global motorDirection
	global motorpin
	global motorpinA
	global motorpinB
	
	if n == 1:
		motorDirection = 1
		motorpin = motorpinA
		if motorRunning == 0:
			motorRunning = 1
			gpio.digitalWrite(motorpin,gpio.HIGH)
		else:
			motorRunning = 0
			gpio.digitalWrite(motorpinA,gpio.LOW)
			gpio.digitalWrite(motorpinB,gpio.LOW)
	elif n == 2:
		motorDirection = 0
		motorpin = motorpinB
		if motorRunning == 0:
			motorRunning = 1
			gpio.digitalWrite(motorpin,gpio.HIGH)
		else:
			motorRunning = 0
			gpio.digitalWrite(motorpinA,gpio.LOW)
			gpio.digitalWrite(motorpinB,gpio.LOW)

def numericCallback(n): # Pass 1 (next setting) or -1 (prev setting)
	global screenMode
	global numberstring
	if n < 10:
		numberstring = numberstring + str(n)
	elif n == 10:
		numberstring = numberstring[:-1]
	elif n == 11:
		screenMode = 1
	elif n == 12:
		screenMode = returnScreen
		numeric = int(numberstring)
		v[dict_idx] = numeric

def settingCallback(n): # Pass 1 (next setting) or -1 (prev setting)
	global screenMode
	screenMode += n
	if screenMode < 1:               screenMode = len(buttons) - 1
	elif screenMode >= len(buttons): screenMode = 1

def valuesCallback(n): # Pass 1 (next setting) or -1 (prev setting)
	global screenMode
	global returnScreen
	global numberstring
	global numeric
	global v
	global dict_idx

	if n == -1:
		screenMode = 0
		saveSettings()
	if n == 1:
		dict_idx='Pulse'
		numberstring = str(v[dict_idx])
		screenMode = 2
		returnScreen = 1
	elif n == 2:
		dict_idx='Interval'
		numberstring = str(v[dict_idx])
		screenMode = 2
		returnScreen = 1
	elif n == 3:
		dict_idx='Images'
		numberstring = str(v[dict_idx])
		screenMode = 2
		returnScreen = 1

def viewCallback(n): # Viewfinder buttons
	global screenMode, screenModePrior
	if n is 0:   # Gear icon
	  screenMode = 1

def doneCallback(): # Exit settings
	global screenMode
	if screenMode > 0:
	  saveSettings()
	screenMode = 0 # Switch back to main window

def startCallback(n): # start/Stop the timelapse thread
	global t, busy, threadExited
	global currentframe
	if n == 1: 
		if busy == False:
			if (threadExited == True):
				# Re-instanciate the object for the next start
				t = threading.Thread(target=timeLapse)
				threadExited = False
			t.start()
	if n == 0:
		if busy == True:
			busy = False
			t.join()
			currentframe = 0
			# Re-instanciate the object for the next time around.
			t = threading.Thread(target=timeLapse)

def timeLapse():
	global v
	global settling_time
	global shutter_length
	global motorpin
	global shutterpin
	global backlightpin
	global busy, threadExited
	global currentframe

	busy = True

	for i in range( 1 , v['Images'] + 1 ):
		if busy == False:
			break
		currentframe = i
		gpio.digitalWrite(motorpin,gpio.HIGH)
		pulse = float(v['Pulse'])/1000.0
		sleep(pulse)
		gpio.digitalWrite(motorpin,gpio.LOW)
		sleep(settling_time)

		# disable the backlight, critical for night timelapses, also saves power
		os.system("echo '0' > /sys/class/gpio/gpio252/value")
		gpio.digitalWrite(shutterpin,gpio.HIGH)
		sleep(shutter_length)
		gpio.digitalWrite(shutterpin,gpio.LOW)
		#  enable the backlight
		os.system("echo '1' > /sys/class/gpio/gpio252/value")
		interval = float(v['Interval'])/1000.0
		if (interval > shutter_length):
			sleep(interval - shutter_length)
	currentframe = 0
	busy = False
	threadExited = True


def signal_handler(signal, frame):
	print 'got SIGTERM'
	pygame.quit()
	sys.exit()

# Global stuff -------------------------------------------------------------

t = threading.Thread(target=timeLapse)
busy            = False
threadExited    = False
screenMode      =  0      # Current screen mode; default = viewfinder
screenModePrior = -1      # Prior screen mode (for detecting changes)
iconPath        = 'icons' # Subdirectory containing UI bitmaps (PNG format)
numeric         = 0       # number from numeric keypad      
numberstring	= "0"
motorRunning	= 0
motorDirection	= 0
returnScreen   = 0
shutterpin     = 17
motorpinA      = 18
motorpinB      = 27
motorpin       = motorpinA
backlightpin   = 252
currentframe   = 0
framecount     = 100
settling_time  = 0.2
shutter_length = 0.2
interval_delay = 0.2
dict_idx	   = "Interval"
v = { "Pulse": 100,
	"Interval": 3000,
	"Images": 150}

icons = [] # This list gets populated at startup

# buttons[] is a list of lists; each top-level list element corresponds
# to one screen mode (e.g. viewfinder, image playback, storage settings),
# and each element within those lists corresponds to one UI button.
# There's a little bit of repetition (e.g. prev/next buttons are
# declared for each settings screen, rather than a single reusable
# set); trying to reuse those few elements just made for an ugly
# tangle of code elsewhere.

buttons = [

  # Screen mode 0 is main view screen of current status
  [Button((  5,180,120, 60), bg='start', cb=startCallback, value=1),
   Button((130,180, 60, 60), bg='cog',   cb=viewCallback, value=0),
   Button((195,180,120, 60), bg='stop',  cb=startCallback, value=0)],

  # Screen 1 for changing values and setting motor direction
  [Button((260,  0, 60, 60), bg='cog',   cb=valuesCallback, value=1),
   Button((260, 60, 60, 60), bg='cog',   cb=valuesCallback, value=2),
   Button((260,120, 60, 60), bg='cog',   cb=valuesCallback, value=3),
   Button((  0,180,160, 60), bg='ok',    cb=valuesCallback, value=-1),
   Button((160,180, 70, 60), bg='left',  cb=motorCallback, value=1),
   Button((230,180, 70, 60), bg='right', cb=motorCallback, value=2)],

  # Screen 2 for numeric input
  [Button((  0,  0,320, 60), bg='box'),
   Button((180,120, 60, 60), bg='0',     cb=numericCallback, value=0),
   Button((  0,180, 60, 60), bg='1',     cb=numericCallback, value=1),
   Button((120,180, 60, 60), bg='3',     cb=numericCallback, value=3),
   Button(( 60,180, 60, 60), bg='2',     cb=numericCallback, value=2),
   Button((  0,120, 60, 60), bg='4',     cb=numericCallback, value=4),
   Button(( 60,120, 60, 60), bg='5',     cb=numericCallback, value=5),
   Button((120,120, 60, 60), bg='6',     cb=numericCallback, value=6),
   Button((  0, 60, 60, 60), bg='7',     cb=numericCallback, value=7),
   Button(( 60, 60, 60, 60), bg='8',     cb=numericCallback, value=8),
   Button((120, 60, 60, 60), bg='9',     cb=numericCallback, value=9),
   Button((240,120, 80, 60), bg='del',   cb=numericCallback, value=10),
   Button((180,180,140, 60), bg='ok',    cb=numericCallback, value=12),
   Button((180, 60,140, 60), bg='cancel',cb=numericCallback, value=11)]
]


# Assorted utility functions -----------------------------------------------


def saveSettings():
	global v
	try:
	  outfile = open('lapse.pkl', 'wb')
	  # Use a dictionary (rather than pickling 'raw' values) so
	  # the number & order of things can change without breaking.
	  pickle.dump(v, outfile)
	  outfile.close()
	except:
	  pass

def loadSettings():
	global v
	try:
	  infile = open('lapse.pkl', 'rb')
	  v = pickle.load(infile)
	  infile.close()
	except:
	  pass



# Initialization -----------------------------------------------------------

# Init framebuffer/touchscreen environment variables
os.putenv('SDL_VIDEODRIVER', 'fbcon')
os.putenv('SDL_FBDEV'      , '/dev/fb1')
os.putenv('SDL_MOUSEDRV'   , 'TSLIB')
os.putenv('SDL_MOUSEDEV'   , '/dev/input/touchscreen')


# Init pygame and screen
print "Initting..."
pygame.init()
print "Setting Mouse invisible..."
pygame.mouse.set_visible(False)
print "Setting fullscreen..."
modes = pygame.display.list_modes(16)
screen = pygame.display.set_mode(modes[0], FULLSCREEN, 16)

print "Loading Icons..."
# Load all icons at startup.
for file in os.listdir(iconPath):
  if fnmatch.fnmatch(file, '*.png'):
    icons.append(Icon(file.split('.')[0]))
# Assign Icons to Buttons, now that they're loaded
print"Assigning Buttons"
for s in buttons:        # For each screenful of buttons...
  for b in s:            #  For each button on screen...
    for i in icons:      #   For each icon...
      if b.bg == i.name: #    Compare names; match?
        b.iconBg = i     #     Assign Icon to Button
        b.bg     = None  #     Name no longer used; allow garbage collection
      if b.fg == i.name:
        b.iconFg = i
        b.fg     = None

# Set up GPIO pins
print "Init GPIO pins..."
gpio = wiringpi2.GPIO(wiringpi2.GPIO.WPI_MODE_GPIO)  
gpio.pinMode(shutterpin,gpio.OUTPUT)  
gpio.pinMode(motorpinA,gpio.OUTPUT)
gpio.pinMode(motorpinB,gpio.OUTPUT)
gpio.pinMode(motorpinB,gpio.OUTPUT)
gpio.digitalWrite(motorpinA,gpio.LOW)
gpio.digitalWrite(motorpinB,gpio.LOW)
# I couldnt seem to get at pin 252 for the backlight using the usual method above, 
# but this seems to work
os.system("echo 252 > /sys/class/gpio/export")
os.system("echo 'out' > /sys/class/gpio/gpio252/direction")
os.system("echo '1' > /sys/class/gpio/gpio252/value")

print"Load Settings"
loadSettings() # Must come last; fiddles with Button/Icon states

print "loading background.."
img    = pygame.image.load("icons/LapsePi.png")

if img is None or img.get_height() < 240: # Letterbox, clear background
  screen.fill(0)
if img:
  screen.blit(img,
    ((320 - img.get_width() ) / 2,
     (240 - img.get_height()) / 2))
pygame.display.update()
sleep(2)

# Main loop ----------------------------------------------------------------

signal.signal(signal.SIGTERM, signal_handler)

print "mainloop.."
while(True):

  # Process touchscreen input
  while True:
    for event in pygame.event.get():
      if(event.type is MOUSEBUTTONDOWN):
        pos = pygame.mouse.get_pos()
        for b in buttons[screenMode]:
          if b.selected(pos): break
      elif(event.type is MOUSEBUTTONUP):
        motorRunning = 0
        gpio.digitalWrite(motorpinA,gpio.LOW)
        gpio.digitalWrite(motorpinB,gpio.LOW)

    if screenMode >= 0 or screenMode != screenModePrior: break


  if img is None or img.get_height() < 240: # Letterbox, clear background
    screen.fill(0)
  if img:
    screen.blit(img,
      ((320 - img.get_width() ) / 2,
       (240 - img.get_height()) / 2))

  # Overlay buttons on display and update
  for i,b in enumerate(buttons[screenMode]):
    b.draw(screen)
  if screenMode == 2:
    myfont = pygame.font.SysFont("Arial", 50)
    label = myfont.render(numberstring, 1, (255,255,255))
    screen.blit(label, (10, 2))
  if screenMode == 1:
    myfont = pygame.font.SysFont("Arial", 30)
    label = myfont.render("Pulse:" , 1, (255,255,255))
    screen.blit(label, (10, 10))
    label = myfont.render("Interval:" , 1, (255,255,255))
    screen.blit(label, (10, 70))
    label = myfont.render("Frames:" , 1, (255,255,255))
    screen.blit(label, (10,130))

    label = myfont.render(str(v['Pulse']) + "ms" , 1, (255,255,255))
    screen.blit(label, (130, 10))
    label = myfont.render(str(v['Interval']) + "ms" , 1, (255,255,255))
    screen.blit(label, (130, 70))
    label = myfont.render(str(v['Images']) , 1, (255,255,255))
    screen.blit(label, (130,130))

  if screenMode == 0:
    myfont = pygame.font.SysFont("Arial", 30)
    label = myfont.render("Pulse:" , 1, (255,255,255))
    screen.blit(label, (10, 10))
    label = myfont.render("Interval:" , 1, (255,255,255))
    screen.blit(label, (10, 50))
    label = myfont.render("Frames:" , 1, (255,255,255))
    screen.blit(label, (10, 90))
    label = myfont.render("Remaining:" , 1, (255,255,255))
    screen.blit(label, (10,130))

    label = myfont.render(str(v['Pulse']) + "ms" , 1, (255,255,255))
    screen.blit(label, (160, 10))
    label = myfont.render(str(v['Interval']) + "ms" , 1, (255,255,255))
    screen.blit(label, (160, 50))
    label = myfont.render(str(currentframe) + " of " + str(v['Images']) , 1, (255,255,255))
    screen.blit(label, (160, 90))

    intervalLength = float((v['Pulse'] + v['Interval'] + (settling_time*1000) + (shutter_length*1000)))
    remaining = float((intervalLength * (v['Images'] - currentframe)) / 1000)
    sec = timedelta(seconds=int(remaining))
    d = datetime(1,1,1) + sec
    remainingStr = "%dh%dm%ds" % (d.hour, d.minute, d.second)

    label = myfont.render(remainingStr , 1, (255,255,255))
    screen.blit(label, (160, 130))
  pygame.display.update()

  screenModePrior = screenMode

