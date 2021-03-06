#!/usr/bin/env python
"""
"""

__author__ = "Trevor Stanhope"
__version__ = "0.1"

# Libraries
import zmq
import ast
import json
import os
import sys
import time
import numpy as np
from datetime import datetime
from serial import Serial, SerialException
import cv2, cv
import thread
import socket

# Constants
CONFIG_PATH = 'settings.json' 
ROBOT_TYPE = socket.gethostname().split('-')[0]

# Robot
class Robot:

    ## Initialize
    def __init__(self, config_path, robot_type):
        
        # Configuration
        self.load_config(config_path)

        # Type
    
        if robot_type == 'picker' or robot_type == 'delivery':
            self.robot_type = robot_type
        else:
            print "Unrecognized ROBOT_TYPE!"
            exit(1)

        # Initializers
        try:
            self.init_zmq()
            self.init_arduino()
            self.init_cam() # only the picker has a camera
        except:
            self.close()

    ## Close
    def close(self):
        self.pretty_print('WARN', 'Shutdown triggered!')
        sys.exit()
    
    ## Pretty Print
    def pretty_print(self, task, msg):
        date = datetime.strftime(datetime.now(), '%d/%b/%Y:%H:%M:%S')
        print('[%s] %s\t%s' % (date, task, msg))

    ## Load Config File
    def load_config(self, config_path):
        with open(config_path) as config_file:
            settings = json.loads(config_file.read())
            for key in settings:
                try:
                    getattr(self, key)
                except AttributeError as e:
                    setattr(self, key, settings[key])
                        
    ## Initialize ZMQ messenger
    def init_zmq(self):
        try:
            self.context = zmq.Context()
            self.socket = self.context.socket(zmq.REQ)
            self.socket.connect(self.ZMQ_ADDR)
            self.poller = zmq.Poller()
            self.poller.register(self.socket, zmq.POLLIN)
        except Exception as e:
            self.pretty_print('ZMQ', 'Error: %s' % str(e))
            raise e
    
    ## Initialize Arduino
    def init_arduino(self, wait=2.0, attempts=3):
        if self.VERBOSE: self.pretty_print("CTRL", "Initializing Arduino ...") 
        for dev in self.ARDUINO_DEV:
            for i in range(attempts):
                try:
                    self.arduino = Serial(dev + str(i), self.ARDUINO_BAUD, timeout=self.ARDUINO_TIMEOUT)
                    time.sleep(wait)
                    break
                except Exception as e:
                    self.pretty_print('CTRL', 'Error: %s' % str(e))
    
    ## Initialize camera
    def init_cam(self):
        if self.VERBOSE: self.pretty_print("CTRL", "Initializing Camera ...")
        try:
            self.bgr = np.zeros((self.CAMERA_HEIGHT, self.CAMERA_WIDTH, 3))
            self.camera = cv2.VideoCapture(self.CAMERA_INDEX)
            self.camera.set(cv.CV_CAP_PROP_FRAME_WIDTH, self.CAMERA_WIDTH)
            self.camera.set(cv.CV_CAP_PROP_FRAME_HEIGHT, self.CAMERA_HEIGHT)
            self.camera.set(cv.CV_CAP_PROP_SATURATION, self.CAMERA_SATURATION)
            self.camera.set(cv.CV_CAP_PROP_CONTRAST, self.CAMERA_CONTRAST)
            self.camera.set(cv.CV_CAP_PROP_BRIGHTNESS, self.CAMERA_BRIGHTNESS)
            thread.start_new_thread(self.capture_image, ())
        except Exception as e:
            self.pretty_print('CAM', 'Error: %s' % str(e))

    ## Capture image
    def capture_image(self):
        if self.VERBOSE: self.pretty_print("CTRL", "Capturing Image ...")
        while True:
            time.sleep(0.01)
            (s, bgr) = self.camera.read()
            if s:
                self.bgr = bgr
            else:
                self.bgr = np.zeros((self.CAMERA_HEIGHT, self.CAMERA_WIDTH, 3))
    
    ## Send request to server
    def request_action(self, status):
        if self.VERBOSE: self.pretty_print('ZMQ', 'Requesting action from server ...')
        try:
            last_action = status['command']
            bgr = self.bgr
            request = {
                'type' : 'request',
                'robot': self.robot_type,
                'last_action' : last_action,
                'bgr' : bgr.tolist()
            }
            dump = json.dumps(request)
            self.socket.send(dump)
            if self.VERBOSE: self.pretty_print('ZMQ', 'Checking poller ...')
            socks = dict(self.poller.poll(self.ZMQ_TIMEOUT))
            if socks:
                if socks.get(self.socket) == zmq.POLLIN:
                    dump = self.socket.recv(zmq.NOBLOCK)
                    response = json.loads(dump)
                    self.pretty_print('ZMQ', 'Response: %s' % str(response))
                    try:
                        action = response['action']
                        self.pretty_print('ZMQ', 'Action: %s' % str(action))
                        return action
                    except:
                        return None
                else:
                    self.pretty_print('ZMQ', 'Error: Poll Timeout')
            else:
                self.pretty_print('ZMQ', 'Error: Socket Timeout')
                exit(1)
        except Exception as e:
            return None

    ## Exectute robotic action
    def execute_action(self, action, attempts=5, wait=2.0):
        if self.VERBOSE: self.pretty_print('CTRL', 'Interacting with controller ...')
        try:
            self.pretty_print("CTRL", "Command: %s" % str(action))
            self.arduino.write(str(action)) # send command
            time.sleep(wait)
            status = None
            while status == None:
                try:
                    string = self.arduino.readline()
                    status = ast.literal_eval(string) # parse status response
                except SyntaxError as e:
                    self.pretty_print('CTRL', 'Error: %s (%s)' % (str(e), string))
                    time.sleep(wait)
                except ValueError as e:
                    self.pretty_print('CTRL', 'Error: %s (%s)' % (str(e), string))
                    time.sleep(wait)
            self.pretty_print("CTRL", "Status: %s" % status)
            self.last_action = action
            return status
        except Exception as e:
            self.pretty_print('CTRL', 'Error: %s' % str(e))
            status = {
                'command' : '?', 
                'result' : 255
            }
            return status
    ## Run
    def run(self):
        status = {
            'robot' : self.robot_type,
            'command' : '?', # start with nothing
        }
        action = None
        while True:
            try:
                action = self.request_action(status)
                if action:
                    status = self.execute_action(action) #!TODO handle different responses
            except Exception as e:
                self.pretty_print('RUN', 'Error: %s' % str(e))

if __name__ == '__main__':
    robot = Robot(CONFIG_PATH, ROBOT_TYPE)
    robot.run()
