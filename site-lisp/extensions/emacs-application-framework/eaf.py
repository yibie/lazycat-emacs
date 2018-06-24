#! /usr/bin/env python
# -*- coding: utf-8 -*-

# Copyright (C) 2018 Andy Stewart
# 
# Author:     Andy Stewart <lazycat.manatee@gmail.com>
# Maintainer: Andy Stewart <lazycat.manatee@gmail.com>
# 
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# any later version.
# 
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
# 
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <http://www.gnu.org/licenses/>.


from PyQt5 import QtCore
from PyQt5.QtCore import QUrl, Qt, QEvent, QPointF
from PyQt5.QtGui import QPainter, QImage, QColor, QMouseEvent
from PyQt5.QtWebKitWidgets import QWebView
from PyQt5.QtWidgets import QWidget, QApplication
from dbus.mainloop.glib import DBusGMainLoop
from xutils import get_xlib_display
from fake_key_event import fake_key_event
import abc
import dbus
import dbus.service
import functools
import threading
import time

EAF_DBUS_NAME = "com.lazycat.eaf"
EAF_OBJECT_NAME = "/com/lazycat/eaf"

class postGui(QtCore.QObject):
    
    through_thread = QtCore.pyqtSignal(object, object)    
    
    def __init__(self, inclass=True):
        super(postGui, self).__init__()
        self.through_thread.connect(self.on_signal_received)
        self.inclass = inclass
        
    def __call__(self, func):
        self._func = func
        
        @functools.wraps(func)
        def obj_call(*args, **kwargs):
            self.emit_signal(args, kwargs)
        return obj_call
        
    def emit_signal(self, args, kwargs):
        self.through_thread.emit(args, kwargs)
                
    def on_signal_received(self, args, kwargs):
        if self.inclass:
            obj, args = args[0], args[1:]
            self._func(obj, *args, **kwargs)
        else:    
            self._func(*args, **kwargs)

class EAF(dbus.service.Object):
    def __init__(self, args):
        global emacs_xid, emacs_width, emacs_height
        
        dbus.service.Object.__init__(
            self,
            dbus.service.BusName(EAF_DBUS_NAME, bus=dbus.SessionBus()),
            EAF_OBJECT_NAME)
        
        (emacs_xid, emacs_width, emacs_height) = (map(lambda x: int(x), args))
        self.buffer_dict = {}
        self.view_dict = {}
        
    @dbus.service.method(EAF_DBUS_NAME, in_signature="sss", out_signature="")
    def new_buffer(self, buffer_id, app_type, input_content):
        if app_type == "browser":
            self.buffer_dict[buffer_id] = BrowserBuffer(buffer_id, app_type, input_content)
            
    @dbus.service.method(EAF_DBUS_NAME, in_signature="s", out_signature="")
    def update_views(self, args):
        global emacs_xid
        
        view_infos = args.split(",")
        
        # Remove old key from view dict and destroy old view.
        for key in list(self.view_dict):
            if key not in view_infos:
                self.view_dict[key].handle_destroy()
                self.view_dict.pop(key, None)
        
        # Create new view and udpate in view dict.
        if view_infos != ['']:
            for view_info in view_infos:
                if view_info not in self.view_dict:
                    view = View(view_info)
                    self.view_dict[view_info] = view
                    
                    view.trigger_mouse_event.connect(self.send_mouse_event_to_buffer)
                    view.trigger_focus_event.connect(self.focus_emacs_buffer)
                    
    @dbus.service.method(EAF_DBUS_NAME, in_signature="s", out_signature="")
    def kill_buffer(self, buffer_id):
        # Kill all view base on buffer_id.
        for key in list(self.view_dict):
            if buffer_id == self.view_dict[key].buffer_id:
                self.view_dict[key].handle_destroy()
                self.view_dict.pop(key, None)
        
        # Clean buffer from buffer dict.
        if buffer_id in self.buffer_dict:
            self.buffer_dict[buffer_id].handle_destroy()
            self.buffer_dict.pop(buffer_id, None)
    
            
    @dbus.service.method(EAF_DBUS_NAME, in_signature="s", out_signature="")
    def send_key(self, args):
        print("Send key: %s" % args)
        (buffer_id, event_string) = args.split(":")
        
        if buffer_id in self.buffer_dict:
            QApplication.sendEvent(self.buffer_dict[buffer_id].buffer_widget, fake_key_event(event_string))
            
    @dbus.service.signal("com.lazycat.eaf")        
    def focus_emacs_buffer(self, message):
        print("************* %s" % message)
        
    def send_mouse_event_to_buffer(self, buffer_id, view_width, view_height, view_image_width, view_image_height, event):
        print("Send mouse: %s %s" % (buffer_id, event))
        
        global emacs_xid
        
        if buffer_id in self.buffer_dict:
            if event.type() in [QEvent.MouseButtonPress, QEvent.MouseButtonRelease,
                                QEvent.MouseMove, QEvent.MouseButtonDblClick]:
                # Get view render coordinate.
                view_render_x = (view_width - view_image_width) / 2
                view_render_y = (view_height - view_image_height) / 2
                
                # Just send event if response in view image area.
                if (event.x() >= view_render_x) and (event.x() <= view_render_x + view_image_width) and (event.y() >= view_render_y) and (event.y() <= view_render_y + view_image_height):
                    view_sizes = list(map(lambda v: (v.width, v.height), self.view_dict.values()))
                    
                    buffer_width = emacs_width
                    buffer_height = emacs_height
                    
                    if len(view_sizes) > 0:
                        buffer_width, buffer_height = max(view_sizes, key=lambda size: size[0] * size[1])
                                        
                    width_scale = view_width * 1.0 / buffer_width
                    height_scale = view_height * 1.0 / buffer_height
                    image_scale = 1.0
                    if width_scale < height_scale:
                        image_scale = width_scale
                    else:
                        image_scale = height_scale

                    new_event_x = (event.x() - view_render_x) / image_scale
                    new_event_y = (event.y() - view_render_y) / image_scale
                    new_event_pos = QPointF(new_event_x, new_event_y)
                    
                    new_event = QMouseEvent(event.type(),
                                            new_event_pos,
                                            event.button(),
                                            event.buttons(),
                                            event.modifiers())
                    
                    QApplication.sendEvent(self.buffer_dict[buffer_id].buffer_widget, new_event)
                else:
                    print("Do not send event, because event out of view's response area")
            else:
                QApplication.sendEvent(self.buffer_dict[buffer_id].buffer_widget, event)
                        
    def update_buffers(self):
        global emacs_width, emacs_height
        
        while True:
            for buffer in self.buffer_dict.values():
                # Get size list of buffer's views.
                view_sizes = list(map(lambda v: (v.width, v.height), self.view_dict.values()))
                
                # Init buffer size with emacs' size.
                buffer_width = emacs_width
                buffer_height = emacs_height
                
                # Update buffer size with max area view's size,
                # to make each view has the same rendering area after user do split operation in emacs.
                if len(view_sizes) > 0:
                    buffer_width, buffer_height = max(view_sizes, key=lambda size: size[0] * size[1])
                                        
                # Resize buffer.
                buffer.resize_buffer(buffer_width, buffer_height)        
                
                # Update buffer image.
                buffer.update_content()
                
                if buffer.qimage != None:
                    # Render views.
                    for view in list(self.view_dict.values()):
                        if view.buffer_id == buffer.buffer_id:
                            # Scale image to view size.
                            width_scale = view.width * 1.0 / buffer_width
                            height_scale = view.height * 1.0 / buffer_height
                            image_scale = 1.0
                            if width_scale < height_scale:
                                image_scale = width_scale
                            else:
                                image_scale = height_scale

                            view.qimage = buffer.qimage.scaled(buffer_width * image_scale, buffer_height * image_scale)
                            view.background_color = buffer.background_color

                            # Update view.
                            view.update()
                
            time.sleep(0.05)
        
class View(QWidget):
    
    trigger_mouse_event = QtCore.pyqtSignal(str, int, int, int, int, QEvent)
    trigger_focus_event = QtCore.pyqtSignal(str)
    
    def __init__(self, view_info):
        super(View, self).__init__()
        
        # Init widget attributes.
        self.setWindowFlags(Qt.FramelessWindowHint)
        self.setAttribute(Qt.WA_X11DoNotAcceptFocus, True)
        self.setContentsMargins(0, 0, 0, 0)
        
        # Init attributes.
        self.view_info = view_info
        (self.buffer_id, self.x, self.y, self.width, self.height) = view_info.split(":")
        self.x = int(self.x)
        self.y = int(self.y)
        self.width = int(self.width)
        self.height = int(self.height)
        
        self.qimage = None
        self.background_color = None
        
        # Show and resize.
        self.show()
        self.resize(self.width, self.height)
        
        self.installEventFilter(self)
        
        print("Create view: %s" % self.view_info)
        
    def eventFilter(self, obj, event):
        if event.type() in [QEvent.MouseButtonPress, QEvent.MouseButtonRelease,
                            QEvent.MouseMove, QEvent.MouseButtonDblClick, QEvent.Wheel]:
            self.trigger_mouse_event.emit(self.buffer_id, self.width, self.height, self.qimage.width(), self.qimage.height(), event)
            self.trigger_focus_event.emit("{0},{1}".format(event.globalX(), event.globalY()))
                    
        return False
        
    def paintEvent(self, event):
        # Init painter.
        painter = QPainter(self)
        
        # Render background.
        if self.background_color != None:
            painter.setBrush(self.background_color)
            painter.drawRect(0, 0, self.width, self.height)
        
        # Render buffer image in center of view.
        if self.qimage != None:
            render_x = (self.width - self.qimage.width()) / 2
            render_y = (self.height - self.qimage.height()) / 2
            painter.drawImage(QtCore.QRect(render_x, render_y, self.qimage.width(), self.qimage.height()), self.qimage)
        
        # End painter.
        painter.end()
        
    def showEvent(self, event):
        # NOTE: we must reparent after widget show, otherwise reparent operation maybe failed.
        self.reparent()
        
    def reparent(self):
        global emacs_xid
        
        xlib_display = get_xlib_display()
        
        view_xid = self.winId().__int__()
        view_xwindow = xlib_display.create_resource_object("window", view_xid)
        emacs_xwindow = xlib_display.create_resource_object("window", emacs_xid)
        
        view_xwindow.reparent(emacs_xwindow, self.x, self.y)
        
        xlib_display.sync()
        
    def handle_destroy(self):
        if self.qimage != None:
            del self.qimage
            
        self.destroy()
        
        print("Destroy view: %s" % self.view_info)
        
class Buffer(object):
    __metaclass__ = abc.ABCMeta
    
    def __init__(self, buffer_id, app_type, input_content, background_color):
        global emacs_width, emacs_height
        
        self.width = emacs_width
        self.height = emacs_height
        
        self.buffer_id = buffer_id
        self.app_type = app_type
        self.input_content = input_content
                
        self.qimage = None
        self.buffer_widget = None
        self.background_color = background_color
        
    def resize_buffer(self, width, height):
        pass
            
    def handle_destroy(self):
        if self.buffer_widget != None:
            self.buffer_widget.destroy()
            
        if self.qimage != None:
            del self.qimage
            
        print("Destroy buffer: %s" % self.buffer_id)
        
    @postGui()    
    def update_content(self):
        if self.buffer_widget != None:
            qimage = QImage(self.width, self.height, QImage.Format_ARGB32)
            self.buffer_widget.render(qimage)
            self.qimage = qimage
                            
class BrowserBuffer(Buffer):
    def __init__(self, buffer_id, app_type, input_content):
        Buffer.__init__(self, buffer_id, app_type, input_content, QColor(255, 255, 255, 255))
        
        self.buffer_widget = QWebView()
        self.buffer_widget.resize(self.width, self.height)
        self.buffer_widget.setUrl(QUrl(input_content))
        
        print("Create buffer: %s" % buffer_id)
        
    def resize_buffer(self, width, height):
        self.width = width
        self.height = height
        self.buffer_widget.resize(self.width, self.height)
        
if __name__ == "__main__":
    import sys
    import signal
    
    DBusGMainLoop(set_as_default=True) # WARING: only use once in one process
    
    bus = dbus.SessionBus()
    if bus.request_name(EAF_DBUS_NAME) != dbus.bus.REQUEST_NAME_REPLY_PRIMARY_OWNER:
        print("EAF process has startup.")
    else:
        emacs_xid = 0
        emacs_width = emacs_height = 0
        
        app = QApplication(sys.argv)
        
        eaf = EAF(sys.argv[1:])
        
        threading.Thread(target=eaf.update_buffers).start()
        
        print("EAF process start.")
        
        signal.signal(signal.SIGINT, signal.SIG_DFL)
        sys.exit(app.exec_())
