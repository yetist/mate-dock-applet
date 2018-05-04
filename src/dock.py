#!/usr/bin/env python3

"""Provide functionality relating to an application dock

Manage a list of pinned and non-pinned dock apps.

Handle all mouse ui events for docked apps
Respond to window opening and closing events from libwnck
Respond to changes to the Gtk icon theme and update all docked apps
Load and save dock settings (e.g. pinned apps and indicator type)
Respond to Unity API DBus messages so that apps can display counts and
progress meters on their icons

respond to selections made in the applet right click menu, specifically
    : allow apps to be pinned to the dock
    : allow apps to unpinned from the dock
    : allow app icons be to moved to a different position on the dock
    : disply an About dialog
    : display a Preferences dialog
"""

# Copyright (C) 1997-2003 Free Software Foundation, Inc.
#
# This program is free software; you can redistribute it and/or
# modify it under the terms of the GNU General Public License as
# published by the Free Software Foundation; either version 2 of the
# License, or (at your option) any later version.
#
# This program is distributed in the hope that it will be useful, but
# WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the GNU
# General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program; if not, write to the Free Software
# Foundation, Inc., 51 Franklin St, Fifth Floor, Boston, MA
# 02110-1301, USA.
#
# Author:
#     Robin Thompson

# do not change the value of this variable - it will be set during build
# according to the value of the --with-gtk3 option used with .configure
import gi
from . import config

if not config.WITH_GTK3:
    gi.require_version("Gtk", "2.0")
    gi.require_version("Wnck", "1.0")
else:
    gi.require_version("Gtk", "3.0")
    gi.require_version("Wnck", "3.0")

gi.require_version("MatePanelApplet", "4.0")
gi.require_version("Notify", "0.7")
gi.require_version("Bamf", "3")

from gi.repository import Gtk
from gi.repository import Gdk
from gi.repository import MatePanelApplet
from gi.repository import GObject
from gi.repository import Wnck
from gi.repository import GdkPixbuf
from gi.repository import Gio
from gi.repository import GLib
from gi.repository import Notify
from gi.repository import Bamf

import os
import os.path
import sys
import subprocess
from time import sleep
import dbus
from dbus.mainloop.glib import DBusGMainLoop

import docked_app
import dock_prefs
import dock_about
import dock_custom_launcher
import dock_win_list
import dock_action_list
import dock_xml
import dock_color_changer
import docked_app_helpers
import window_control

from log_it import log_it as log_it

class DragMotionTimer(object):
    """Timer to allow us to track mouse motion during a drag and drop
       operation.

    Required because:
        we don't get drag-motion events when dragging app icons
        within the applet and we need to track the mouse x,y so that
        we can rearrange dock icons on the fly

    Instantiates a timer which periodically gets the root x,y position of
    the mouse and translates these to applet x,y coordinate

    Attributes:
        dragee : the docked app which is being dragged
        drag-ended : the drag and drop operation has finished
        timer_id = the id of the timer that is instantiated
        mouse = a Gdk.device we can query for the mouse position

    """

    def __init__(self, dragee, the_dock):
        """Init for the DragMotionTimer class.

        Sets everything up by creating the timer and setting a reference to
        the DockedApp being dragged

        Arguments:
            dragee : the DockedApp that is being dragged
            the_dock : the dock...
        """

        self.dragee = dragee
        self.drag_ended = False
        self.the_dock = the_dock
        self.old_drag_pos = -1

        # call the timer function 50 times per second
        self.timer_id = GObject.timeout_add(20, self.do_timer)

        # get the mouse device
        display = Gdk.Display.get_default()
        manager = display.get_device_manager()
        self.mouse = manager.get_client_pointer()

    def do_timer(self):
        """The timer function.

        If the drag operation has ended, delete the timer

        Move dock icons about etc....

        """

        # has the drag and drop ended?
        if self.drag_ended is True:
            GObject.source_remove(self.timer_id)
            return False

        none, x, y = self.mouse.get_position()

        dx, dy = self.the_dock.get_dock_root_coords()
        x = x - dx
        y = y - dy

        orient = self.the_dock.applet.get_orient()

        app_with_mouse = self.the_dock.get_app_at_mouse(x, y)
        if app_with_mouse is not None:
            app_name = app_with_mouse.app_name
        else:
            # we're not on the dock, so just record the current mouse x
            # or y and exit
            if (orient == MatePanelApplet.AppletOrient.UP) or \
               (orient == MatePanelApplet.AppletOrient.DOWN):
                self.old_drag_pos = x
            else:
                self.old_drag_pos = y

            return True

        if app_with_mouse == self.dragee:
            return True

        # get the coordinates within the applet of the app that has the
        # mouse
        ax, ay = self.the_dock.get_app_root_coords(app_with_mouse)

        ax = ax - dx
        ay = ay - dy

        # if the dock is scrolling we need to add the scrolled window position
        # to the x and y coords
        if self.the_dock.scrolling:
            if (orient == MatePanelApplet.AppletOrient.UP) or \
               (orient == MatePanelApplet.AppletOrient.DOWN):
                ax -= self.the_dock.scrolled_win.get_hadjustment().get_value()
            else:
                ay -= self.the_dock.scrolled_win.get_vadjustment().get_value()

        orient = self.the_dock.applet.get_orient()
        if (orient == MatePanelApplet.AppletOrient.UP) or \
           (orient == MatePanelApplet.AppletOrient.DOWN):
            if self.old_drag_pos == -1:
                self.old_drag_pos = x
                return True

            if x > self.old_drag_pos:
                # we're moving left to right on a new app, so we need to
                # trigger an icon move at 40% of the icon width
                trig_val = ax + (self.dragee.drawing_area_size * 40) / 100
                if (self.old_drag_pos < trig_val) and (x >= trig_val):
                    new_pos = self.the_dock.app_list.index(app_with_mouse)
                    self.the_dock.move_app(self.dragee, new_pos)
            else:
                # moving right to left
                trig_val = ax + self.dragee.drawing_area_size - \
                           (self.dragee.drawing_area_size * 40) / 100
                if (self.old_drag_pos > trig_val) and (x <= trig_val):
                    new_pos = self.the_dock.get_app_position_in_dock(app_with_mouse)
                    self.the_dock.move_app(self.dragee, new_pos)

            self.old_drag_pos = x

        else:

            if self.old_drag_pos == -1:
                self.old_drag_pos = y
                return True

            if y > self.old_drag_pos:
                # we're moving top to bottom on a new app, so we need to
                # trigger an icon move at 40% of the icon height
                trig_val = ay + (self.dragee.drawing_area_size * 40) / 100
                if (self.old_drag_pos < trig_val) and (y >= trig_val):
                    new_pos = self.the_dock.app_list.index(app_with_mouse)
                    self.the_dock.move_app(self.dragee, new_pos)
            else:
                # moving bottom to top
                trig_val = ay + self.dragee.drawing_area_size - \
                           (self.dragee.drawing_area_size * 40) / 100
                if (self.old_drag_pos > trig_val) and (y <= trig_val):
                    new_pos = self.the_dock.get_app_position_in_dock(app_with_mouse)
                    self.the_dock.move_app(self.dragee, new_pos)

            self.old_drag_pos = y

        return True


class DragActivateTimer(object):
    """ Timer used when something other than a .desktop file is dragged onto the
        dock

    Instantiates a timer which will wait for a short interval and then activate
    a specified app's window. This will allow apps to drag data onto the dock,
    activate an app's window and then drop the data there. The short delay between
    the user dragging data onto the dock and the app activating provides a better
    user experience ....

    Attributes:
        the_app : the app whose window is to be acticvated
        timer_id = the id of the timer that is instantiated

    """

    def __init__(self, the_dock, the_app):
        """Init for the DragActionTimer class.

        Sets everything up by creating the timer and setting a reference to
        the DockedApp to be activated

        Arguments:
            the_dock : the dock...
            the_app  : the app to be activated

        """

        self.the_app = the_app
        self.the_dock = the_dock
        # wait .3 of a second ...
        self.timer_id = GObject.timeout_add(333, self.do_timer)

    def do_timer(self):
        """ Activate the app
        """

        if (self.the_app is not None) and (self.the_app.is_running()):
                if not self.the_app.is_active:
                    self.the_dock.minimize_or_restore_windows(self.the_app, None)

        self.timer_id = 0
        return False  # we only run once...


class ScrollAnimator(object):
    """ Class to animate the scrolling of the dock

    Allow scrolling from a start point to an end point in a scrolled window
    over a number of frames with a specified interval. Also allow
    a specified callback to be called when the animation is
    finished


    Attributes:
        __scrolled_win : the window we're interested in
        __start_pos : the starting position of the scroll
        __end_pos   : the ending position
        __orient    : the panel orientation (e.g. "top") - so we know whether to scroll horizontally
                      or vertically
        __num_frames : the number of frames
        __interval   : the interval between frames in ms
        __callback   : the callback for when the animation is finished
        __current_frame : the current animation frame number

    """

    def __init__(self, sw, sp, ep, orient, nf, int, cb):
        self.__scrolled_win = sw
        self.__start_pos = sp
        self.__orient = orient
        self.__end_pos = ep
        self.__num_frames = nf
        self.__interval = int
        self.__callback = cb
        self.__current_frame = 0

        # set the initial position of the scrolled window
        self.set_scroll_pos(self.__start_pos)

        # create a timer to periodically set the scrolled window position
        self.timer_id = GObject.timeout_add(self.__interval, self.do_timer)

    def set_scroll_pos(self, pos):
        """Set the current scroll position

        """
        if self.__orient in ["top", "bottom"]:
            self.__scrolled_win.get_hadjustment().set_value(pos)
        else:
            self.__scrolled_win.get_vadjustment().set_value(pos)

    def do_timer(self):
        """ Update the scrolled window position

            adjustment = (self.__end_pos - self.__start_pos) / self.__num_frames
        """

        self.__current_frame += 1
        if self.__current_frame == self.__num_frames:
            # end of the animation - set the final position of the window and stop the
            # timer
            self.set_scroll_pos(self.__end_pos)

            if self.__callback is not None:
                self.__callback()

            return False
        else:

            new_pos = self.__start_pos + ((self.__end_pos - self.__start_pos) / self.__num_frames) \
                      * self.__current_frame

            self.set_scroll_pos(new_pos)

            return True


class Dock(object):
    """The main application dock class

        Attributes:
            applet : the MATE panel applet
            wnck_screen : the currently active wnck_screen. Assumed to be the
                          default wnck_screen on applet start up
            window :
            app_list : the list of DockedApp objects. Will contain
                       running/non-running pinned apps and running unpinned
                       apps
            box    : A Gtk2 HBox or VBox (depending on the applet orientation)
                     or Gtk3 Grid containing the drawing areas of each of the
                     apps in app_list
            scrolled_win : a scrolled window too hold self.box (gtk3 only, in gtk2
                           self.box is added directly to the applet)
            sw_hadj : a Gtk.Adjustment - used when there isn't enough panel space to fully
                      display the dock and we need to horizontally scroll it
            sw_vadj : as above, but for vertically scrolling
            app_spacing : the amount of space (in pixels) between icons on the dock
            icontheme : used to load application icons and detect changes in
                        the icon theme
            about_win : the about window
            prefs_win : the preferences window
            ccl_win   : the create custom launcher window
            app_with_mouse : the DockedApp that the mouse is currently over
            active_app : the DockedApp that is currently the foreground app
            right_clicked_app: the app that was most recently right clicked
            settings_path : the GIO.Settings path for the applet
            settings : GIO.Settings - the settings for the applet
            indicator : the indicator type (e.g. light, dark, bar or None)
            attention_type : the attention_type e.g. blink
            fallback_bar_col : a list of the r,g,b elements of the colour to be
                               used when drawing e,g, bar indicators and the
                               current theme highlight colour can't be determined
                               (will be mainly of use in gtk2)
            active_bg : the type of background to use for the currently active app's
                        icon background (e.g. gradient fill or solid fill)
            multi_ind : whether or not multiple indicators are to be used
            show_all_apps : whether or not unpinned apps from all workspaces
                                     are displayed in the dock
            win_from_cur_ws_only : whether indicators and window list items are
                                   to be shown for the current workspace only
            change_panel_color : whether or not the color of MATE panels are to
                                 be changed to the dominant colour of the
                                 desktop wallpaper
            change_dock_color_only : whether or not all MATE panels are to have
                                     their colour changed or just the panel
                                     containing the dock
            panel_id : the toplevel id of the panel the applet is on
            dock_action_group : Gtk Action group containing all of the actions
                                for the applet right click menu
            app_win_list : a replacement for the default tooltip - a window
                            which lists the currently highlighted app's open
                            windows and allows the user to select one
            app_act_list :  a popup window which shows actions relating to the
                            currently highlighted app e.g. Pin/Unpin/Open new
                            window
            act_list_timer : timer object used when popping up the action list
            panel_cc : used to change the colour of the MATE panel(s) to
                       the dominant color of the desktop wallpaper (if enabled
                        in the applet settings)
            panel_orient: i.e. 'top', 'bottom', 'left', 'right' -
                          obtained from dconf
            panel_x : the panel's x position on screen
            panel_y : the panel's y position on screen
            panel_size : the size of the panel
            applet_pos : the position of the applet(in pixels) in the panel
            dm_timer : timer to monitor mouse movement during app icon drag and drop
            da_timer : timer to provide a short delay before activating an app when
                       data other than a .desktop file is dragged on the applet
            popup_delay : the delay (in ms) before an action list appears when the
                          mouse hovers over a docked app
            pa_configs : list of pinned app configurations. each item in the list
                         is a tuple containing the following:
                           string : the config name
                           string : the name of the workspace the config is associated with
                           string : a .dektop filename representing a pinned app
                           string : another pinned app, etc. etc.
            pa_on_all_ws : boolean - if true, pinned apps appear on all workspaces.
                                     If false, pinned apps are set according to the current workspace
            notification : the latest unpin notification

            matcher : a Bamf.Matcher for matching apps with their windows and .desktop files etc
            avail_panel_space : a tuple containing the amount of panel space (x & y) available to the dock
            scrolling : set to true when the dock doesn't have enough panel space to display
                        itself and needs to scroll
            scroll_index : the index in self.app_list of the first visible item when scrolling is
                           enabled
            scroll_timer : a timer to scroll the apps in the dock when the mouse hovers over an app icon

            dock_fixed_size : indicates that the dock is not to expand or contract and will instead
                              claim enough space to display the specified number of apps. If set -1
                              the dock will in fact expand or contract
            panel_layout   : the name of the current panel layout e.g. "mutiny"
            nice_sizing : whether or not we can use applet.set_size_hints to allow dock to
                          request a size from the panel
            ns_base_apps : the minimum size of the dock (in app icons) to be used when nice_sizing
                           is True
            ns_new_app   : when nice_sizing is True, is used to indicate whether the last
                           call to set_size_hints was in response to a new app being
                           added to the dock
            ns_app_removed : when nice_sizing is True, specifies the visible index of the app
                             that was removed from the dock, or None if no app was removed
            dds_done       : when True, indicates that delayed setup has been completed and
                             the applet is now fully setup

            drag_x : x coordinate of mouse where dragging began
            drag_y : y coordinate of mouse where dragging began
            dragging : are we dragging dock icons about


    """

    def __init__(self, applet):
        """Init the Dock.

        Load settings
        Setup the applet right click menu and actions
        Set default values
        """

        super().__init__()

        Notify.init("Mate Dock Applet")
        self.applet = applet    # the panel applet, in case we need it later

        self.app_list = []
        self.box = None
        if config.WITH_GTK3:
            self.scrolled_win = Gtk.ScrolledWindow()
            self.scrolled_win.set_policy(Gtk.PolicyType.EXTERNAL, Gtk.PolicyType.EXTERNAL)
            self.scrolled_win.connect("scroll-event", self.window_scroll)

        self.icontheme = Gtk.IconTheme.get_default()
        self.icontheme.connect("changed", self.icon_theme_changed)

        self.window = None

        self.wnck_screen = Wnck.Screen.get_default()

        self.app_with_mouse = None      # the dock app that mouse is currently over
        self.active_app = None          # the currently active app
        self.right_clicked_app = None   # the app that most recently had a right click

        self.settings_path = self.applet.get_preferences_path()
        self.settings = Gio.Settings.new_with_path("org.mate.panel.applet.dock",
                                                   self.settings_path)

        # instantiate these - will be set up later
        self.object_settings = None
        self.panel_settings = None
        self.panel_event_handler_id = None
        self.panel_id = ""
        self.panel_layout = ""
        self.get_panel_layout()

        # specify the xml file to be used as an alternative storage location
        # for the applet settings
        self.xml_conf = os.path.expanduser("~/.config/mate_dock_applet.conf")

        self.prefs_win = None
        self.about_win = None
        self.ccl_win = None
        self.indicator = 0
        self.fallback_bar_col = None
        self.multi_ind = False
        self.click_restore_last_active = True
        self.show_all_apps = True
        self.win_from_cur_ws_only = False
        self.use_win_list = True
        self.panel_act_list = False
        self.change_panel_color = False
        self.change_dock_color_only = False
        self.active_bg = 0
        self.app_spacing = 0
        self.attention_type = dock_prefs.AttentionType.BLINK
        self.popup_delay = 1000
        self.pa_configs = []
        self.pa_on_all_ws = True
        self.dock_fixed_size = -1
        self.ns_new_app = False
        self.dds_done = False
        self.ns_app_removed = None
        self.read_settings()
        self.set_fallback_bar_colour()

        # read the list of apps which are difficult to match with their
        # .desktop files
        self.app_match = self.read_app_match()
        self.dock_action_group = None
        self.popup_action_group = None

        self.app_win_list = dock_win_list.DockWinList(self.wnck_screen, self.applet.get_orient(), 0)
        self.app_win_list.icontheme = self.icontheme
        self.app_act_list = dock_action_list.DockActionList(self.wnck_screen,
                                                            self.applet.get_orient(), 0)
        self.app_act_list.icontheme = self.icontheme
        self.act_list_timer = None

        self.panel_x = 0
        self.panel_y = 0
        self.panel_size = 0
        self.panel_orient = "top"
        self.applet_pos = 0

        self.dm_timer = None
        self.da_timer = None

        self.notification = None

        self.avail_panel_space = (1, 1)
        self.scrolling = False
        self.scroll_index = 0
        self.scroll_timer = None

        self.sw_hadj = Gtk.Adjustment(0, 0, 1, 1, 1, 1)
        self.sw_vadj = Gtk.Adjustment(0, 0, 1, 1, 1, 1)

        # set a callback so that the window_control module can account for dock scrolling
        # when calculating window minimise positions
        window_control.adj_minimise_pos_cb = self.adjust_minimise_pos

        # create an event handler so that we can react to changes e.g
        # the panel the applet is on, or it's position on the panel
        applet_spath = self.settings_path[0: len(self.settings_path)-6]  # remove "prefs/" suffix
        self.object_settings = Gio.Settings.new_with_path("org.mate.panel.object", applet_spath)
        self.object_settings.connect("changed",
                                     self.applet_panel_settings_changed)

        self.setup_menu()

        self.panel_cc = dock_color_changer.PanelColorChanger()

        # we need to monitor the Unity dbus interface
        DBusGMainLoop(set_as_default=True)
        self.session_bus = dbus.SessionBus()

        # claim the Unity bus (Unity won't be using it...) so that clients know
        # to start using it
        self.session_bus.request_name("com.canonical.Unity",
                                      dbus.bus.NAME_FLAG_ALLOW_REPLACEMENT)

        # add a handler to listen in Unity dbus messages
        self.session_bus.add_signal_receiver(self.unity_cb_handler,
                                             dbus_interface="com.canonical.Unity.LauncherEntry",
                                             signal_name="Update")

        # create a Bamf.Matcher for matching windows to running apps
        self.matcher = Bamf.Matcher.get_default()

        # can we resize nicely on the panel?
        try:
            self.applet.set_size_hints([100, 0], 0)
            self.nice_sizing = True
            self.ns_base_apps = 4
        except TypeError:
            self.nice_sizing = False
            self.ns_base_apps = 0

        self.drag_x = self.drag_y = -1
        self.dragging = False

        # instantiate a timer to perform further setup once the applet has been
        # fully created
        GObject.timeout_add(1000, self.do_delayed_setup)

    def __del__(self):
        """ Clean up ...
        """

        # release the unity bus
        self.session_bus.release_name("com.canonical.Unity")

    def do_delayed_setup(self):
        """ Perform setup operations that we couldn't do until the dock was
            fully instantiated

            Get the id of the panel the applet is on
            Do the initial panel colour change (if necessary)
            Set the minimise locations of open windows

        """

        self.get_panel_id()

        # now that we have our panel id, we can set use it to set to panel
        # colour changer if necessary
        if self.change_dock_color_only:
            self.panel_cc.set_single_panel(self.panel_id)
        else:
            self.panel_cc.set_single_panel("")

        # enable panel colour changing?
        if self.change_panel_color:
            self.panel_cc.enable_color_change()
            # we're starting up so need to do an initial panel colour change
            self.panel_cc.do_change_panel_color()

        # get info about the panel the applet is on
        self.get_applet_panel_info()

        # TODO: we wont need this when applet.set_size_hints and when MATE 1.20
        # has been rolled out to all distros...
        # get the current panel layout
        self.get_panel_layout()

        # constrain the dock's size so that it does not overlap any other applets
        if config.WITH_GTK3:
            if self.nice_sizing:
                # set the applet's size hints
                self.set_size_hints()

            elif self.panel_layout.upper() == "MUTINY":
                self.dock_fixed_size = self.get_mutiny_fixed_size()
                self.avail_panel_space = self.get_avail_panel_space()
                self.set_dock_panel_size()
                self.write_settings()
            else:
                self.avail_panel_space = self.get_avail_panel_space()
                self.set_dock_panel_size()

        self.set_all_apps_minimise_targets()

        # if panel layout is mutiny and there are no saved settings files
        # then create one. Workaround for launchpad bug #1755835
        # https://bugs.launchpad.net/ubuntu/+source/mate-dock-applet/+bug/1755835?comments=all
        if self.panel_layout.upper() == "MUTINY":
            if not os.path.isfile(self.xml_conf):
                self.write_settings()

        self.dds_done = True
        return False        # cancel the timer

    def get_panel_id(self):
        """ Get the toplevel id of the panel the applet is on
        """

        # get the info from dconf settings
        self.panel_id = self.object_settings.get_string("toplevel-id")

    def get_applet_panel_custom_rgb(self):
        """ If the panel containing the applet has a custom background
            colour, return the colour

            Requires get_applet_panel_info to have been called at least
            once beforehand

        Returns:
            A tuple of 3 x int : the r,g,b components or None if a custom
            colour is not being used
        """

        settings_path = "/org/mate/panel/toplevels/%s/background/" % self.panel_id

        # get this panel's background settings
        psettings = Gio.Settings.new_with_path("org.mate.panel.toplevel.background",
                                               settings_path)

        if psettings.get_string("type") == "none":
            # colours from the current theme are being used
            return None
        else:
            # get the panel's custom colour rgb components
            colstr = psettings.get_string("color")

            if (colstr.startswith("rgba")) or (colstr.startswith("rgb")):
                colstrip = colstr.strip("rgba()")
                cols = colstrip.split(",")
                pr = int(cols[0])
                pg = int(cols[1])
                pb = int(cols[2])
            else:
                pr = int(colstr[1:3], 16)
                pg = int(colstr[3:5], 16)
                pb = int(colstr[5:7], 16)

        return [pr, pg, pb]

    def get_panel_layout(self):
        """ Get the current panel layout from dconf"""
        psettings = Gio.Settings.new("org.mate.panel")
        self.panel_layout = psettings.get_string("default-layout")

    def get_panel_height(self, mypanel, orients):
        """ Returns the combined heights of the panels with the specified orientations"

        Args:
            mypanel - the toplevel id of the panel the applet is on
            orients - a list of panel orientation we're interested in. Will typically
                      contain "top", "bottom" or both)

        Returns:
            The height of the specified panel(s), or 0 is there were no panels found with the
            specified orientations
        """

        # get this panel's settings
        plist = Gio.Settings.new("org.mate.panel")

        panel_heights = 0

        toplevels = plist.get_value("toplevel-id-list").unpack()
        for toplevel in toplevels:
            if mypanel != toplevel:
                tl_path = "/org/mate/panel/toplevels/%s/" % toplevel

                # get this panel's settings
                tpsettings = Gio.Settings.new_with_path("org.mate.panel.toplevel",
                                                        tl_path)

                # get the info
                panel_orient = tpsettings.get_string("orientation")
                if panel_orient in orients:
                    panel_heights += tpsettings.get_int("size")

        return panel_heights

    def get_applet_panel_info(self):
        """ Read info from dconf regarding the panel the applet is on, and also
            the applet's position on the panel

            Note: the toplevel id of the panel must already have been read
        """

        self.applet_pos = self.object_settings.get_int("position")

        # get the settings path for the current panel
        settings_path = "/org/mate/panel/toplevels/%s/" % self.panel_id

        # get this panel's settings

        if self.panel_event_handler_id is not None:
            self.panel_settings.disconnect(self.panel_event_handler_id)

        try:
            self.panel_settings = Gio.Settings.new_with_path("org.mate.panel.toplevel",
                                                             settings_path)
        except TypeError:
            # this can happen when the applet quits and it is therefore
            # safe to ignore
            return

        # get the info
        self.panel_orient = self.panel_settings.get_string("orientation")
        self.panel_x = self.panel_settings.get_int("x")
        self.panel_y = self.panel_settings.get_int("y")
        self.panel_size = self.panel_settings.get_int("size")

        # if this is left or right oriented panel we need to account for the
        # height of any top panel so that minimise locations are correctly
        # calculated...
        if (self.panel_orient == "left") or (self.panel_orient == "right"):
            self.applet_pos += self.get_panel_height(self.panel_id, ["top"])

        # finally, connect an event handler so that if this panel's settings
        # are changed i.e. orientation, size, we can respond to this
        self.panel_event_handler_id = self.panel_settings.connect("changed",
                                                                  self.panel_settings_changed)

    def applet_panel_settings_changed(self, settings, key):
        """ Callback for when the the applet settings with regards to it's
            panel are changed

            If the panel the applet is on is changed, update our panel id and
            recalculate all docked apps minimize positions accordingly

            If the applet's position on its panel is changed, the minimize
            positions of all docked apps also need to be minimized
        """

        if key == "toplevel-id":
            self.get_panel_id()
            if self.change_dock_color_only:
                self.panel_cc.set_single_panel(self.panel_id)

            # remove any scroll indicators and reset the scroll positions
            if self.panel_id != "":
                if self.scrolling:
                    self.set_app_scroll_dirs(False)
                    self.reset_scroll_position()

            if self.scrolling and not self.nice_sizing:
                self.set_app_scroll_dirs(True)

            self.get_applet_panel_info()
            self.set_all_apps_minimise_targets()

            if config.WITH_GTK3:
                if not self.nice_sizing:
                    self.avail_panel_space = self.get_avail_panel_space()
                    self.set_dock_panel_size()

        if key == "position":
            # we can get here during applet creation when we still might not
            # know out panel id so...
            if self.panel_id != "":
                if not self.nice_sizing:
                    if self.scrolling:
                        self.set_app_scroll_dirs(False)
                        self.reset_scroll_position()

                self.get_applet_panel_info()
                self.set_all_apps_minimise_targets()

                if config.WITH_GTK3:
                    if not self.nice_sizing:
                        self.avail_panel_space = self.get_avail_panel_space()
                        self.set_dock_panel_size()

                        if self.scrolling:
                            self.set_app_scroll_dirs(True)

    def panel_settings_changed(self, settings, key):
        """ Callback for the settings of the panel the applet is on changed

        If the size or orientation of the panel changes, we need to recalculate
        app minimise locations
        """

        if (key == "orientation") or (key == "size"):
            self.get_applet_panel_info()
            if config.WITH_GTK3:
                self.set_dock_panel_size()
            self.set_all_apps_minimise_targets()
            for app in self.app_list:
                app.queue_draw()

    def read_settings(self):
        """ Read the current dock settings from dconf

            If this particular dock has not been run before and a settings xml
            file exists, import the settings and apply them to the new dock

        """

        # is this dock being run for the first time?
        if self.settings.get_boolean("first-run") is True:
            # this dock is being run for the first time, so if we have any
            # saved settings from other docks, import them. Note: prior to
            # V0.70 the applet presented a dialog asking the user whether or
            # not to import the previous settings. This dialog has been removed
            # and the previous settings are now silently imported to prevent
            # problems when swtiching to the Mutiny layout in Ubuntu
            # Mate 16.04

            xml_settings = dock_xml.read_xml(self.xml_conf)

            if xml_settings[0] is True:
                # the settings were read correctly, so set everything up

                pinned_apps = []
                for pinned_app in xml_settings[1]:
                    pinned_apps.append(pinned_app)

                self.indicator = xml_settings[2]
                self.show_all_apps = xml_settings[3]
                self.multi_ind = xml_settings[4]
                self.use_win_list = xml_settings[5]
                self.win_from_cur_ws_only = xml_settings[6]
                self.change_panel_color = xml_settings[7]
                self.change_dock_color_only = xml_settings[8]
                self.panel_act_list = xml_settings[9]
                self.active_bg = xml_settings[10]

                self.fallback_bar_col = []
                for col in xml_settings[11]:
                    self.fallback_bar_col.append(col)

                self.app_spacing = xml_settings[12]
                self.attention_type = xml_settings[13]
                self.popup_delay = xml_settings[14]
                self.pa_configs = xml_settings[15]
                self.pa_on_all_ws = xml_settings[16]
                self.dock_fixed_size = xml_settings[17]

                # now, immediately write the settings to dconf and back to the
                # config file so the dock can access them

                configs = self.configs_to_settings()

                self.settings.set_value("pinned-apps", GLib.Variant('as',
                                        pinned_apps))
                self.settings.set_int("indicator-type", self.indicator)
                self.settings.set_boolean("multi-ind", self.multi_ind)
                self.settings.set_boolean("apps-from-all-workspaces",
                                          self.show_all_apps)
                self.settings.set_boolean("first-run", False)
                self.settings.set_boolean("use-win-list",
                                          self.use_win_list)
                self.settings.set_boolean("win-from-cur-workspace-only",
                                          self.win_from_cur_ws_only)
                self.settings.set_boolean("change-panel-color",
                                          self.change_panel_color)
                self.settings.set_boolean("change-panel-color-dock-only",
                                          self.change_dock_color_only)
                self.settings.set_boolean("panel-act-list",
                                          self.panel_act_list)
                self.settings.set_int("bg-type", self.active_bg)
                self.settings.set_value("fallback-bar-col", GLib.Variant('as',
                                        self.fallback_bar_col))

                self.settings.set_int("app-spacing", self.app_spacing)
                self.settings.set_int("attention-type", self.attention_type)
                self.settings.set_int("popup-delay", self.popup_delay)
                self.settings.set_boolean("pinned-apps-on-all-workspaces", self.pa_on_all_ws)
                self.settings.set_value("saved-configs", GLib.Variant('as', configs))
                self.settings.set_int("dock-fixed-size", self.dock_fixed_size)

                dock_xml.write_xml(self.xml_conf, pinned_apps, self.indicator,
                                   self.show_all_apps, self.multi_ind,
                                   self.use_win_list,
                                   self.win_from_cur_ws_only,
                                   self.change_panel_color, self.change_dock_color_only,
                                   self.panel_act_list,
                                   self.active_bg,
                                   self.fallback_bar_col,
                                   self.app_spacing,
                                   self.attention_type,
                                   self.popup_delay,
                                   self.pa_configs,
                                   self.pa_on_all_ws,
                                   self.dock_fixed_size)

                return

        # we get here if there was no previous configuration, or the
        # configuration couldn't be read.
        # Where the configuration couldn't be read this could be due to an
        # error or because new versions of the applet have added configuration
        # options not yet in the user's xml file. To recover, simply assume a
        # default set of options
        self.indicator = self.settings.get_int("indicator-type")
        self.multi_ind = self.settings.get_boolean("multi-ind")
        self.show_all_apps = self.settings.get_boolean("apps-from-all-workspaces")
        self.use_win_list = self.settings.get_boolean("use-win-list")
        self.click_restore_last_active = self.settings.get_boolean("click-restore-last-active")
        self.change_panel_color = self.settings.get_boolean("change-panel-color")
        self.change_dock_color_only = self.settings.get_boolean("change-panel-color-dock-only")
        self.panel_act_list = self.settings.get_boolean("panel-act-list")
        self.active_bg = self.settings.get_int("bg-type")
        self.fallback_bar_col = self.settings.get_value("fallback-bar-col").unpack()
        self.app_spacing = self.settings.get_int("app-spacing")
        self.attention_type = self.settings.get_int("attention-type")
        self.popup_delay = self.settings.get_int("popup-delay")
        self.pa_on_all_ws = self.settings.get_boolean("pinned-apps-on-all-workspaces")
        self.dock_fixed_size = self.settings.get_int("dock-fixed-size")

    def configs_to_settings(self):
        """ Convenience function to convert the current set of saved configs
            into a form that can to be written to dconf settings i.e. a list of
            csv strings
        """

        pa_configs = []
        for config in self.pa_configs:
            item = '"' + config[0] + "," + config[1]
            for loop in range(2, len(config)):
                item += "," + config[loop]
            item += '"'
            pa_configs.append(item)

        return pa_configs

    def write_settings(self):
        """Write the current dock settings.

        Write a list of all of the currently pinned apps .desktop files
        Write the indicator type, whether to use multiple indicators,
        and whether to show unpinned apps from all workspaces etc.

        Set the first-run indicator to False
        """

        pinned_apps = []
        if self.pa_on_all_ws:
            for dock_app in self.app_list:
                if dock_app.desktop_file is not None and dock_app.is_pinned:
                    pinned_apps.append(os.path.basename(dock_app.desktop_file))
        else:
            # get the original pinned app configuration from dconf, so
            # it can be written to the .xml config file.
            # Doing so allows the pinned app configuration to be restored on
            # if the user reverts the pa_on_all_ws setting, even if the applet
            # is deleted from the panel and then add it again
            pinned_apps = self.settings.get_value("pinned-apps").unpack()

        pa_configs = self.configs_to_settings()

        if self.settings:
            # only save the current set of self.pinned apps if we're not inking pinned apps
            # to workspaces
            if self.pa_on_all_ws:
                self.settings.set_value("pinned-apps", GLib.Variant('as', pinned_apps))

            self.settings.set_int("indicator-type", self.indicator)
            self.settings.set_boolean("multi-ind", self.multi_ind)
            self.settings.set_boolean("apps-from-all-workspaces",
                                      self.show_all_apps)
            self.settings.set_boolean("win-from-cur-workspace-only",
                                      self.win_from_cur_ws_only)
            self.settings.set_boolean("use-win-list",
                                      self.use_win_list)
            self.settings.set_boolean("change-panel-color",
                                      self.change_panel_color)
            self.settings.set_boolean("change-panel-color-dock-only",
                                      self.change_dock_color_only)
            self.settings.set_boolean("panel-act-list", self.panel_act_list)
            self.settings.set_int("bg-type", self.active_bg)
            self.settings.set_boolean("first-run", False)
            self.settings.set_value("fallback-bar-col", GLib.Variant('as', self.fallback_bar_col))
            self.settings.set_int("app-spacing", self.app_spacing)
            self.settings.set_int("attention-type", self.attention_type)
            self.settings.set_int("popup-delay", self.popup_delay)
            self.settings.set_boolean("pinned-apps-on-all-workspaces", self.pa_on_all_ws)
            self.settings.set_value("saved-configs", GLib.Variant('as', pa_configs))
            self.settings.set_int("dock-fixed-size", self.dock_fixed_size)

        dock_xml.write_xml(self.xml_conf, pinned_apps, self.indicator,
                           self.show_all_apps, self.multi_ind,
                           self.use_win_list,
                           self.win_from_cur_ws_only,
                           self.change_panel_color,
                           self.change_dock_color_only,
                           self.panel_act_list,
                           self.active_bg,
                           self.fallback_bar_col,
                           self.app_spacing,
                           self.attention_type,
                           self.popup_delay,
                           self.pa_configs,
                           self.pa_on_all_ws,
                           self.dock_fixed_size)

    def read_app_match(self):
        """ Read an xml file which contains a list of apps which are difficult
            to match with their respective .desktop file.

        Returns:
            A list of tuples which containing the following:
                the app name (as reported by wnck)
                the app's wm_class (as reported by wnck)
                the .desktop file to be used by apps whose name or wm_class
                match the above
        """

        d, f = os.path.split(os.path.abspath(__file__))
        results = dock_xml.read_app_xml("%s/app_match.xml" % d)
        if results[0]:
            # the file was read successfully
            return results[1]
        else:
            return []

    def set_fallback_bar_colour(self):
        """ Set the colour to be used for drawing bar and other types of indicators when
            the highlight colour from the theme can't be determined
        """

        r = int(self.fallback_bar_col[0]) / 255
        g = int(self.fallback_bar_col[1]) / 255
        b = int(self.fallback_bar_col[2]) / 255

        docked_app_helpers.fallback_ind_col = [r, g, b]

    def get_docked_app_by_desktop_file(self, dfname):
        """ Returns the docked app which has the same destop file name as dfname

        Params: dfname - the filename of the .desktop file (note: any path is ignored, only
                         the actual file name is taken into account

        Returns a docked app if a match for dfname is found, None otherwise
        """

        df = os.path.basename(dfname)
        for app in self.app_list:
            if app.desktop_file is not None:
                if os.path.basename(app.desktop_file) == df:
                    return app

        return None

    def get_docked_app_by_bamf_app(self, bamf_app):
        """
            Returns the docked_app relating to the running bamf_app

            Params:
                bamf_app: the bamf_app of the application

            Returns a docked_app, or None if it could not be found

        """

        if bamf_app is not None:
            for app in self.app_list:
                if app.has_bamf_app(bamf_app):
                    return app

        return None

    def get_docked_app_by_bamf_window(self, bamf_win):
        """ Returns the docked app which owns the specified window

        Params:
            bamf_window: the Bamf.Window in

        Returns a docked app, or None if it could not be found

        """

        if bamf_win is not None:
            for app in self.app_list:
                if app.has_bamf_window(bamf_win):
                    return app

        return None

    def set_actions_for_app(self, app):
        """Show or hide actions in the context menu and action list so that only relevant ones
           are shown for the specified app

           If the app is pinned, do not show the pin actions.

           If the app in not pinned, don't show the unpin actions.

           Depending on applet orientation actions to move the app left/right
           or up/down along the dock need to shown or hidden.

           If the app is the first or last on the dock, then the options to
           move it up/left or down/right will also need to be hidden

           Include the app name in the menu text e.g. Pin Caja to the dock

           If the app has more than one window on screen, show actions allowing
           the user to select one

           If the app is running and has one or more windows on screen, show
           an option allowing them to be closed

           If the app does not have a desktop file, show an option allowing a
           custom launcher to be created

           Show any right click options specified in the app's desktop file

        Args:
            app : The DockedApp
        """

        def set_vis(action, vis):
            """ convenience function to set an action's visibilty

            Args:
                action : the action
                vis    : boolean - True if the action is to be visible, False otherwise
            """

            action.set_visible(vis)

        # hide all actions which can appear either in the panel right click menu or popop action list
        #
        # they'll be shown again later depending on which is being used...

        # if we have have no app, set all actions invisible and exit...
        panel_act_visible = self.panel_act_list and (app is not None)
        popup_act_visible = (not self.panel_act_list) and (app is not None)
        # first, hide actions defined in the desktop file...
        act = self.dock_action_group.get_action("df_shortcut_1_action")
        set_vis(act, panel_act_visible)
        act = self.dock_action_group.get_action("df_shortcut_2_action")
        set_vis(act, panel_act_visible)
        act = self.dock_action_group.get_action("df_shortcut_3_action")
        set_vis(act, panel_act_visible)
        act = self.dock_action_group.get_action("df_shortcut_4_action")
        set_vis(act, panel_act_visible)

        act = self.popup_action_group.get_action("df_shortcut_1_action")
        set_vis(act, popup_act_visible)
        act = self.popup_action_group.get_action("df_shortcut_2_action")
        set_vis(act, popup_act_visible)
        act = self.popup_action_group.get_action("df_shortcut_3_action")
        set_vis(act, popup_act_visible)
        act = self.popup_action_group.get_action("df_shortcut_4_action")
        set_vis(act, popup_act_visible)

        # now do pin and unpin actions
        act = self.dock_action_group.get_action("pin_action")
        set_vis(act, panel_act_visible)
        act = self.dock_action_group.get_action("unpin_action")
        set_vis(act, panel_act_visible)

        act = self.popup_action_group.get_action("pin_action")
        set_vis(act, popup_act_visible)
        act = self.popup_action_group.get_action("unpin_action")
        set_vis(act, popup_act_visible)

        close_win_action = self.dock_action_group.get_action("close_win_action")
        close_win_action.set_visible(False)

        # TODO: decide - do we need ccl anymore???
        ccl_action = self.dock_action_group.get_action("ccl_action")
        ccl_action.set_visible(False)

        if app is None:
            return

        # now setup the relevant actions panel/action list items
        if self.panel_act_list:
            df_shortcut_1_action = self.dock_action_group.get_action("df_shortcut_1_action")
            df_shortcut_2_action = self.dock_action_group.get_action("df_shortcut_2_action")
            df_shortcut_3_action = self.dock_action_group.get_action("df_shortcut_3_action")
            df_shortcut_4_action = self.dock_action_group.get_action("df_shortcut_4_action")
        else:
            df_shortcut_1_action = self.popup_action_group.get_action("df_shortcut_1_action")
            df_shortcut_2_action = self.popup_action_group.get_action("df_shortcut_2_action")
            df_shortcut_3_action = self.popup_action_group.get_action("df_shortcut_3_action")
            df_shortcut_4_action = self.popup_action_group.get_action("df_shortcut_4_action")

        act_exists, act_name = app.get_rc_action(1)
        df_shortcut_1_action.set_visible(act_exists)
        if act_exists is True:
            df_shortcut_1_action.set_label(act_name)
            df_shortcut_1_action.set_icon_name(app.icon_name)

        act_exists, act_name = app.get_rc_action(2)
        df_shortcut_2_action.set_visible(act_exists)
        if act_exists is True:
            df_shortcut_2_action.set_label(act_name)
            df_shortcut_2_action.set_icon_name(app.icon_name)

        act_exists, act_name = app.get_rc_action(3)
        df_shortcut_3_action.set_visible(act_exists)
        if act_exists is True:
            df_shortcut_3_action.set_label(act_name)
            df_shortcut_3_action.set_icon_name(app.icon_name)

        act_exists, act_name = app.get_rc_action(4)
        df_shortcut_4_action.set_visible(act_exists)
        if act_exists is True:
            df_shortcut_4_action.set_label(act_name)
            df_shortcut_4_action.set_icon_name(app.icon_name)

        if self.panel_act_list:
            pin_action = self.dock_action_group.get_action("pin_action")
            unpin_action = self.dock_action_group.get_action("unpin_action")
        else:
            pin_action = self.popup_action_group.get_action("pin_action")
            unpin_action = self.popup_action_group.get_action("unpin_action")

        # pin/unpin actions don't appear when we don't have a .desktop file...
        if not app.has_desktop_file():
            pin_action.set_visible(False)
            unpin_action.set_visible(False)
        else:
            pin_action.set_visible(not app.is_pinned)
            unpin_action.set_visible(app.is_pinned)

        if pin_action.is_visible():
            pin_action.set_label("Pin %s" % app.app_name)
        else:
            unpin_action.set_label("Unpin %s" % app.app_name)

        move_up_action = self.dock_action_group.get_action("move_up_action")
        move_down_action = self.dock_action_group.get_action("move_down_action")
        move_left_action = self.dock_action_group.get_action("move_left_action")
        move_right_action = self.dock_action_group.get_action("move_right_action")

        index = self.get_app_position_in_dock(app)

        orientation = self.applet.get_orient()

        if orientation == MatePanelApplet.AppletOrient.LEFT or \
           orientation == MatePanelApplet.AppletOrient.RIGHT:
            move_up_action.set_visible(index > 0)
            move_down_action.set_visible(index < (len(self.box.get_children()))-1)
            move_left_action.set_visible(False)
            move_right_action.set_visible(False)
            move_up_action.set_label("Move %s up the dock" % app.app_name)
            move_down_action.set_label("Move %s down the dock" % app.app_name)
        else:
            move_up_action.set_visible(False)
            move_down_action.set_visible(False)
            move_left_action.set_visible(index > 0)
            move_right_action.set_visible(index < (len(self.box.get_children()))-1)
            move_left_action.set_label("Move %s to the left on the dock" % app.app_name)
            move_right_action.set_label("Move %s to the right on the dock" % app.app_name)

        # set the actions for selecting specific windows

        num_win = app.get_num_windows()
        if num_win == 1:
            close_win_action.set_label("Close %s" % app.app_name)
        else:
            close_win_action.set_label("Close all windows")

        if num_win > 0:
            close_win_action.set_visible(True)

        # ccl_action.set_visible(not app.has_desktop_file())
        # now setup the actions which can appear in either the right click menu or
        # action list, depending on which is being used

    def setup_menu(self):
        """Set up the actions and right click menu for the applet. Also setup
           the actions for the popup action list
        """

        # actions named df_shortcut_<x>_action are used for implementing
        # shortcuts/actions specified in an app's .desktop file

        self.dock_action_group = Gtk.ActionGroup("DockActions")
        self.dock_action_group.add_actions([
                            ("df_shortcut_1_action", None,
                             "df_shortcut_1_action", None, "df_shortcut_1_action",
                             self.df_shortcut_1),
                            ("df_shortcut_2_action", None,
                             "df_shortcut_2_action", None, "df_shortcut_2_action",
                             self.df_shortcut_2),
                            ("df_shortcut_3_action", None,
                             "df_shortcut_3_action", None, "df_shortcut_3_action",
                             self.df_shortcut_3),
                            ("df_shortcut_4_action", None,
                             "df_shortcut_4_action", None, "df_shortcut_4_action",
                             self.df_shortcut_4),
                            ("pin_action", Gtk.STOCK_ADD,
                             "_Pin app to the dock", None, "Pin app to the dock",
                             self.pin_app),
                            ("unpin_action", Gtk.STOCK_REMOVE,
                             "_Unpin app from the dock", None, "Unpin app from the dock",
                             self.unpin_app),
                            ("move_up_action", Gtk.STOCK_GO_UP,
                             "Move app _up the dock", None, "Move app up the dock",
                             self.move_app_up),
                            ("move_down_action", Gtk.STOCK_GO_DOWN,
                             "Move app _down the dock", None, "Move app down the dock",
                             self.move_app_down),
                            ("move_left_action", Gtk.STOCK_GO_BACK,
                             "Move app _left in the dock", None, "Move app left in the dock",
                             self.move_app_up),
                            ("move_right_action", Gtk.STOCK_GO_FORWARD,
                             "Move app _right in the dock", None, "Move app right in the dock",
                             self.move_app_down),
                            ("prefs_action", Gtk.STOCK_PREFERENCES,
                             "Dock P_references", None, "Dock Preferences",
                             self.show_prefs_win),
                            ("ccl_action", Gtk.STOCK_EXECUTE,
                             "Create custo_m launcher for this app", None, "Create custom launcher for this app",
                             self.show_ccl_win),
                            ("about_action", Gtk.STOCK_ABOUT,
                             "About...", None, "About...", self.show_about_win),
                            ("close_win_action", Gtk.STOCK_CLOSE,
                             "_Close", None, "Close", self.close_win)
                             ])

        menu_xml = '<menuitem name="df_shortcut_1_action" action="df_shortcut_1_action"/><separator/>'
        menu_xml += '<menuitem name="df_shortcut_2_action" action="df_shortcut_2_action"/><separator/>'
        menu_xml += '<menuitem name="df_shortcut_3_action" action="df_shortcut_3_action"/><separator/>'
        menu_xml += '<menuitem name="df_shortcut_4_action" action="df_shortcut_4_action"/><separator/>'

        menu_xml += '<menuitem name="close_win" action="close_win_action"/>'

        # we only need menu items for moving app icons for Gtk2
        # (Gtk3 does it with drag and drop)
        if not config.WITH_GTK3:
            menu_xml += '<separator/><menuitem name="move_up" action="move_up_action"/>'
            menu_xml += '<menuitem name="move_down" action="move_down_action"/>'
            menu_xml += '<menuitem name="move_left" action="move_left_action"/>'
            menu_xml += '<menuitem name="move_right" action="move_right_action"/>'

        menu_xml += '<separator/><menuitem name="Pin" action="pin_action"/>'
        menu_xml += '<menuitem name="Unpin" action="unpin_action"/><separator/>'

        menu_xml += '<menuitem name="Preferences" action="prefs_action"/>'
        menu_xml += '<menuitem name="Create custom launcher" action="ccl_action"/>'
        menu_xml += '<menuitem name="About" action="about_action"/><separator/>'

        self.applet.setup_menu(menu_xml, self.dock_action_group)

        # setup the action list items - pin/unpin options plus actions defined in the
        # desktop file.
        self.popup_action_group = Gtk.ActionGroup("PopupActions")
        self.popup_action_group.add_actions([
            ("df_shortcut_1_action", None,
             "df_shortcut_1_action", None, "df_shortcut_1_action",
             self.df_shortcut_1),
            ("df_shortcut_2_action", None,
             "df_shortcut_2_action", None, "df_shortcut_2_action",
             self.df_shortcut_2),
            ("df_shortcut_3_action", None,
             "df_shortcut_3_action", None, "df_shortcut_3_action",
             self.df_shortcut_3),
            ("df_shortcut_4_action", None,
             "df_shortcut_4_action", None, "df_shortcut_4_action",
             self.df_shortcut_4),
            ("pin_action", Gtk.STOCK_ADD,
             "Pin app to the dock", None, "Pin app to the dock",
             self.pin_app),
            ("unpin_action", Gtk.STOCK_REMOVE,
             "_Unpin app from the dock", None, "Unpin app from the dock",
             self.unpin_app)
            ])

    def df_shortcut_1(self, data=None):
        """Perform the app's 1st .desktop file specified shortcut/action
        """

        # get the app in question
        if self.panel_act_list:
            the_app = self.right_clicked_app
        else:
            if self.app_act_list.get_visible():
                the_app = self.app_act_list.the_app
            else:
                the_app = self.right_clicked_app

        if the_app is not None:
            the_app.run_rc_action(1)

    def df_shortcut_2(self, data=None):
        """Perform the app's 1st .desktop file specified shortcut/action
        """

        # get the app in question
        if self.panel_act_list:
            the_app = self.right_clicked_app
        else:
            if self.app_act_list.get_visible():
                the_app = self.app_act_list.the_app
            else:
                the_app = self.right_clicked_app

        if the_app is not None:
            the_app.run_rc_action(2)

    def df_shortcut_3(self, data=None):
        """Perform the app's 1st .desktop file specified shortcut/action
        """

        # get the app in question
        if self.panel_act_list:
            the_app = self.right_clicked_app
        else:
            if self.app_act_list.get_visible():
                the_app = self.app_act_list.the_app
            else:
                the_app = self.right_clicked_app

        if the_app is not None:
            the_app.run_rc_action(3)

    def df_shortcut_4(self, data=None):
        """Perform the app's 1st .desktop file specified shortcut/action

        The app is specfied from the winwdow list
        """

        # get the app in question
        if self.panel_act_list:
            the_app = self.right_clicked_app
        else:
            if self.app_act_list.get_visible():
                the_app = self.app_act_list.the_app
            else:
                the_app = self.right_clicked_app

        if the_app is not None:
            the_app.run_rc_action(4)

    def update_pinned_app_config(self):
        """
            Updates the pinned app configuration data for the current workspace from
            self.app_list

            Should be called when apps are pinned/unpinned, change positions on the dock etc.

        """

        ws = self.wnck_screen.get_active_workspace()
        if ws is not None:
            ws_name = ws.get_name()

            # get the list of currently pinned apps from self.app_list
            app_list = []
            for app in self.app_list:
                if app.is_pinned:
                    app_list.append(os.path.basename(app.desktop_file))

            # update the config with the new list
            newconf = []
            index = 0
            for config in self.pa_configs:
                if config[1] == ws_name:
                    # copy the config name and workspace

                    newconf = [config[0], config[1]]
                    for app in app_list:
                        newconf.append(app)

                    self.pa_configs.remove(config)
                    self.pa_configs.append(newconf)
                    break

                index += 1

            if newconf == []:
                # this is a new configuration, so append it to the list
                # (config name can be the same as the workspace name for now)

                newconf = [ws_name, ws_name]
                for app in app_list:
                    newconf.append(app)

                self.pa_configs.append(newconf)

    def unpin_app(self, data=None):
        """Unpin an app from the dock

        This action is performed from the action list or the pane;
        right click menu

        Unpin the app and update the dock settings.

        If the app is not running, remove it from the dock also
        """

        # get the app in question
        if self.panel_act_list:
            the_app = self.right_clicked_app
        else:
            if self.app_act_list.get_visible():
                the_app = self.app_act_list.the_app
            else:
                the_app = self.right_clicked_app

        if the_app is not None:
            # get the index of the app in self.app list
            app_index = self.app_list.index(the_app)

            the_app.is_pinned = False
            if not the_app.is_running():
                self.remove_app_from_dock(the_app)
                self.set_all_apps_minimise_targets()
                self.right_clicked_app = None

            # if we're pinning apps to specific workspaces, remove the app from
            # the workspace its pinned to
            if not self.pa_on_all_ws:
                self.update_pinned_app_config()

                # if the app is running, but has no windows on the current workspace
                # it needs to be removed from the dock
                if the_app.is_running():
                    cur_ws = self.wnck_screen.get_active_workspace()
                    if not the_app.has_windows_on_workspace(cur_ws):
                        self.remove_app_from_dock(the_app)
                        self.set_all_apps_minimise_targets()
                        self.right_clicked_app = None

            # maintain a reference to the unpin notification...
            self.notification = Notify.Notification.new("%s unpinned" % the_app.app_name)
            self.notification.set_icon_from_pixbuf(the_app.app_pb)
            self.notification.add_action("action_click", "Undo", self.notify_cb, [the_app, app_index])
            self.notification.show()

            self.write_settings()

    def notify_cb(self, notification, action, app_data):
        """
            Callback for 'Unpin' notification

            Pin the app back onto the dock in the same position and workspace as it was
            unpinned from


        Params:
            notification: the notification
            action: the action performed i.e. mouse click
            app_data :  a tuple containing the docked_app that was unpinned, and the index in self.app_list
                        from which the app was unpinned

        """

        the_app = app_data[0]

        # is the app still in the dock?
        app_in_dock = False
        for app in self.app_list:
            if app == the_app:
                app_in_dock = True
                break

        the_app.is_pinned = True
        if not app_in_dock:
            # the app is not in the dock, so add it again and then move to the required
            # position
            self.app_list.append(the_app)
            self.add_app(the_app)
            self.move_app(the_app, app_data[1])

        notification = Notify.Notification.new("%s re-pinned" % the_app.app_name)
        notification.set_icon_from_pixbuf(the_app.app_pb)
        notification.show()

        # write settings...
        if not self.pa_on_all_ws:
            self.update_pinned_app_config()
        self.write_settings()

    def pin_app(self, data=None):
        """Pin an app to the dock.

        Pin the app and update the dock settings"""

        # get the app in question
        if self.panel_act_list:
            the_app = self.right_clicked_app
        else:
            if self.app_act_list.get_visible():
                the_app = self.app_act_list.the_app
            else:
                the_app = self.right_clicked_app

        if the_app is not None:
            the_app.is_pinned = True

            # if we're pinning apps to specific workspaces, add the app to
            # the current workspaces saved configuration
            if not self.pa_on_all_ws:
                self.update_pinned_app_config()

            self.write_settings()

    def get_app_position_in_dock(self, app):
        """ Get the position of a specified app in the dock.

        Args : app - A DockedApp

        Returns : the index of the app, or -1 if it wasn't found
        """

        index = 0
        hidden = 0

        for app_da in self.box.get_children():

            if app_da == app.drawing_area:
                if not config.WITH_GTK3:
                    return index
                else:
                    if self.box.orientation == Gtk.Orientation.HORIZONTAL:
                        boxi = self.box.child_get_property(app_da,
                                                           "left-attach")
                    else:
                        boxi = self.box.child_get_property(app_da,
                                                           "top_attach")
                    return boxi

            index += 1

        return -1

    def move_app_up(self, data=None):
        """ Move the right clicked app up one position on the dock (or left if the
            panel is on the top or bottom of the screen).

        Moves the app and then recaculates the minimize location for it's
        windows.

        Writes the dock settings once all is done.
        """

        if self.right_clicked_app is not None:

            index = self.get_app_position_in_dock(self.right_clicked_app)
            if index > 0:

                app = self.app_list[index-1]
                # we need to move the app both in self.applist and self.box
                if not config.WITH_GTK3:
                    self.box.reorder_child(self.right_clicked_app.drawing_area, index-1)
                else:
                    if self.box.orientation == Gtk.Orientation.HORIZONTAL:
                        prop = "left-attach"
                    else:
                        prop = "top-attach"

                    self.box.child_set_property(self.right_clicked_app.drawing_area, prop, index-1)
                    self.box.child_set_property(app.drawing_area, prop, index)

                self.app_list[index-1] = self.app_list[index]
                self.app_list[index] = app

                # allow Gtk to perform the move
                while Gtk.events_pending():
                    Gtk.main_iteration()

                # recalculate the minimize targets for each app
                self.set_minimise_target(self.app_list[index-1])
                self.set_minimise_target(self.app_list[index])

                if not self.pa_on_all_ws:
                    self.update_pinned_app_config()

                self.write_settings()

    def move_app(self, the_app, new_pos):
        """ Move a docked app to a new position in the dock, adjusting the
            the positions of other apps as necessary

        This is used during drag and drop operatations and when repinning
        unpinned apps in response to notifications

        Args:
            the_app : the docked_app we're moving
            new_pos : int, the new position in the docked
        """

        old_pos = self.app_list.index(the_app)
        if self.scrolling:
            self.set_app_scroll_dirs(False)

        # first move the app's drawing area
        if not config.WITH_GTK3:
            self.box.reorder_child(the_app.drawing_area, new_pos)
        else:
            if self.box.orientation == Gtk.Orientation.HORIZONTAL:
                prop = "left-attach"
            else:
                prop = "top-attach"

            if new_pos > old_pos:
                step = 1
            else:
                step = -1

            i = old_pos + step

            # first, adjust the contents of the box containing the app drawing areas
            while i != new_pos + step:
                app_to_move = self.app_list[i]
                self.box.child_set_property(app_to_move.drawing_area,
                                            prop, i - step)
                i += step

            # move the desired app's drawing area
            self.box.child_set_property(the_app.drawing_area, prop, new_pos)

        # now move things around in the app list to match
        self.app_list.remove(the_app)
        self.app_list.insert(new_pos, the_app)

        # allow Gtk toperform the move
        while Gtk.events_pending():
            Gtk.main_iteration()

        if self.scrolling:
            self.set_app_scroll_dirs(True)

        # we need to redraw the icons and recalculate the minimise positions
        # of all apps
        for app in self.app_list:
            app.queue_draw()
        self.set_all_apps_minimise_targets()

        # save the new settings
        if not self.pa_on_all_ws:
            self.update_pinned_app_config()
        self.write_settings()

    def get_app_root_coords(self, app):
        """ Calculate and return the root x and y co-ordinates of the top left
            pixel of a docked app

        Args:
            app: the docked app

        Returns:
            two integers, the x and y coordinates
        """

        dock_x, dock_y = self.get_dock_root_coords()

        x, y, w, h = app.get_allocation()
        dock_x += x
        dock_y += y
        return dock_x, dock_y

    def get_dock_root_coords(self):
        """ Get the root coords of the top left pixel of the dock

        Returns:
            two integers, the x and y coordinates
        """

        # get root coord from the applet window rather from panel settings...
        win = self.applet.props.window
        # check validity of win - can be None during applet creation...
        if win is None:
            return 0, 0

        if not config.WITH_GTK3:
            # win.get_origin doesn't work on gtk2, so...
            dock_x, dock_y = win.get_root_coords(0, 0)
        else:
            thing, dock_x, dock_y = win.get_origin()

        return dock_x, dock_y

    def set_minimise_target(self, app, win=None):
        """ Calculate and set the minimise locations for an app's windows,
            or just for a single window

        Args:
            app: the docked_app
            win : a single window which needs its minimise location set
        """

        min_x, min_y = self.get_app_root_coords(app)

        # its more visually appealing if we minimize to the centre of the app's
        #  icon, so reduce the size of the minimize areas and adjust the
        # coordinates ...
        adj = app.drawing_area_size/4
        min_x += adj
        min_y += adj
        app_w = app_h = app.drawing_area_size - (adj*2)

        if win is None:
            app.set_all_windows_icon_geometry(min_x, min_y, app_w, app_h)
        else:
            window_control.set_minimise_target(win, min_x, min_y, app_w, app_h)

    def set_all_apps_minimise_targets(self):
        """ Calculate and set the window minimise locations for all app's
        """

        for app in self.app_list:
            self.set_minimise_target(app)

    def move_app_down(self, data=None):
        """ Move the right clicked app down one position on the dock (or right
            if the panel is on the top or bottom of the screen).

        Moves the app and then recaculates the minimize location for it's
        windows.

        Writes the dock settings once all is done.
        """

        if self.right_clicked_app is not None:

            index = self.get_app_position_in_dock(self.right_clicked_app)
            if index < len(self.box.get_children())-1:

                app = self.app_list[index+1]

                # we need to move the app both in self.applist and self.box
                if not config.WITH_GTK3:
                    self.box.reorder_child(self.right_clicked_app.drawing_area,
                                           index+1)
                else:
                    if self.box.orientation == Gtk.Orientation.HORIZONTAL:
                        prop = "left-attach"
                    else:
                        prop = "top-attach"

                    self.box.child_set_property(self.right_clicked_app.drawing_area,
                                                prop, index+1)
                    self.box.child_set_property(app.drawing_area, prop, index)

                self.app_list[index+1] = self.app_list[index]
                self.app_list[index] = app

                # allow Gtk to move perform the move
                while Gtk.events_pending():
                    Gtk.main_iteration()

                # recalculate the minimize targets for each app
                self.set_minimise_target(self.app_list[index+1])
                self.set_minimise_target(self.app_list[index])

                if not self.pa_on_all_ws:
                    self.update_pinned_app_config()
                self.write_settings()

    def show_prefs_win(self, data=None):
        """ Show the preferences window.

        If, necessary create the window and register a callback for the 'ok'
        button press

        If the window has already been shown, just show it again.
        """

        if self.prefs_win is None:
            self.prefs_win = dock_prefs.DockPrefsWindow(self.prefs_win_ok_cb,
                                                        self.app_list[0])
            self.prefs_win.set_indicator(self.indicator)
            self.prefs_win.set_multi_ind(self.multi_ind)
            self.prefs_win.set_show_unpinned_apps_on_all_ws(self.show_all_apps)
            self.prefs_win.set_use_win_list(self.use_win_list)
            self.prefs_win.set_change_panel_color(self.change_panel_color)
            self.prefs_win.set_change_dock_color_only(self.change_dock_color_only)
            self.prefs_win.set_pan_act(self.panel_act_list)
            self.prefs_win.set_win_cur_ws_only(self.win_from_cur_ws_only)
            self.prefs_win.set_bg(self.active_bg)
            self.prefs_win.set_fallback_bar_col(self.fallback_bar_col)
            self.prefs_win.set_app_spacing(self.app_spacing)
            self.prefs_win.set_attention_type(self.attention_type)
            self.prefs_win.set_popup_delay(self.popup_delay)
            self.prefs_win.set_show_pinned_apps_on_all_ws(self.pa_on_all_ws)
            if not build_gtk2 and not self.nice_sizing:
                self.prefs_win.set_fixed_size(self.dock_fixed_size != -1, self.dock_fixed_size,
                                              self.panel_layout.upper() == "MUTINY")
        else:
            self.prefs_win.show_all()

        if self.nice_sizing:
            # we don't need the dock size options
            self.prefs_win.set_dock_size_visible(False)

    def show_about_win(self, data=None):
        """ Show the About window.

        If, necessary create the window and show it.

        If the window has already been shown, just show it again.
        """
        if self.about_win is None:
            self.about_win = dock_about.AboutWindow()

        self.about_win.show_all()

    def prefs_win_ok_cb(self, widget, event):
        """ Callback for the 'ok' button on the preferences window.

        If the preferences have been changed then:
            write the new settings
            redraw each running app in app_list with the new indicator type

        Args:
            widget - the button the caused the event
            event - the event args
        """
        if config.WITH_GTK3:
            if self.panel_layout.upper() == "MUTINY":
                fixed_size_changes = False
            else:
                prefs_fixed_size, prefs_num_icons = self.prefs_win.get_fixed_size()
                if not prefs_fixed_size:
                    prefs_num_icons = -1  # indicate a varaible size
                fixed_size_changes = prefs_num_icons != self.dock_fixed_size
        else:
            fixed_size_changes = False

        if (self.indicator != self.prefs_win.get_indicator_type()) or \
           (self.multi_ind != self.prefs_win.get_multi_ind()) or \
           (self.show_all_apps != self.prefs_win.get_show_unpinned_apps_on_all_ws()) or \
           (self.win_from_cur_ws_only != self.prefs_win.get_win_cur_ws_only()) or \
           (self.use_win_list != self.prefs_win.get_use_win_list()) or \
           (self.change_panel_color != self.prefs_win.get_change_panel_color()) or \
           (self.change_dock_color_only != self.prefs_win.get_change_dock_color_only()) or \
           (self.panel_act_list != self.prefs_win.get_pan_act()) or \
           (self.active_bg != self.prefs_win.get_bg()) or \
           (self.fallback_bar_col != self.prefs_win.get_fallback_bar_col()) or \
           (self.app_spacing != self.prefs_win.get_app_spacing()) or \
           (self.attention_type != self.prefs_win.get_attention_type()) or \
           (self.popup_delay != self.prefs_win.get_popup_delay()) or \
           (self.pa_on_all_ws != self.prefs_win.get_show_pinned_apps_on_all_ws()) or \
           fixed_size_changes:

            old_ind = self.indicator
            self.indicator = self.prefs_win.get_indicator_type()
            self.multi_ind = self.prefs_win.get_multi_ind()
            self.show_all_apps = self.prefs_win.get_show_unpinned_apps_on_all_ws()
            self.win_from_cur_ws_only = self.prefs_win.get_win_cur_ws_only()
            self.use_win_list = self.prefs_win.get_use_win_list()
            self.app_spacing = self.prefs_win.get_app_spacing()
            self.attention_type = self.prefs_win.get_attention_type()
            self.popup_delay = self.prefs_win.get_popup_delay()

            new_panel_color_setting = self.change_panel_color != self.prefs_win.get_change_panel_color()
            self.change_panel_color = self.prefs_win.get_change_panel_color()

            self.change_dock_color_only = self.prefs_win.get_change_dock_color_only()
            if self.change_dock_color_only:
                self.panel_cc.set_single_panel(self.panel_id)
            else:
                self.panel_cc.set_single_panel("")

            if self.panel_act_list != self.prefs_win.get_pan_act():
                self.panel_act_list = self.prefs_win.get_pan_act()

            self.active_bg = self.prefs_win.get_bg()

            self.fallback_bar_col = self.prefs_win.get_fallback_bar_col()
            self.set_fallback_bar_colour()

            if self.pa_on_all_ws != self.prefs_win.get_show_pinned_apps_on_all_ws():
                self.pa_on_all_ws = self.prefs_win.get_show_pinned_apps_on_all_ws()

                self.clear_dock_apps()
                self.setup_app_list()
                self.setup_dock_apps()
                self.set_all_apps_minimise_targets()

            if fixed_size_changes:
                if self.scrolling:
                    self.set_app_scroll_dirs(False)
                    self.reset_scroll_position()

                self.dock_fixed_size = prefs_num_icons
                self.set_dock_panel_size()

            self.write_settings()

            # redraw everything here

            if not config.WITH_GTK3:
                self.box.set_spacing(self.app_spacing + 2)
            else:
                self.box.set_row_spacing(self.app_spacing + 2)
                self.box.set_column_spacing(self.app_spacing + 2)

            if old_ind != self.indicator:
                # if the new indicator requires a different amount of space than the
                # old one did then we need to remove all of the drawing areas from
                # the box/grid, set new size requests for each app and then re-add them

                if docked_app_helpers.ind_extra_s(self.indicator) != docked_app_helpers.ind_extra_s(old_ind):

                    size = self.applet.get_size()
                    for app in self.app_list:
                        self.box.remove(app.drawing_area)
                        app.set_indicator(self.indicator)
                        app.set_drawing_area_size(size)  # request a new size

                    for app in self.app_list:
                        self.add_app(app)  # add the drawing area back to the box

                else:
                    # if there is no size difference, just set the new indicator
                    for app in self.app_list:
                        app.set_indicator(self.indicator)

            for app in self.app_list:
                app.set_multi_ind(self.multi_ind)
                app.set_active_bg(self.active_bg)
                app.set_attention_type(self.attention_type)
                if app.is_running():
                    app.queue_draw()
            self.show_or_hide_app_icons()
            self.show_or_hide_indicators()

            if new_panel_color_setting:
                # panel colour changing setting has been changed so we need to
                # enable or disable colour changing
                if self.change_panel_color:
                    self.panel_cc.enable_color_change()
                    self.panel_cc.do_change_panel_color()
                else:
                    self.panel_cc.disable_color_change()

        self.prefs_win.hide()

    def show_ccl_win(self, data=None):
        """ Show the create custom launcher window.

        If, necessary create the window and register a callback for the 'ok'
        button press

        If the window has already been shown, clear all of the fields
        before showing it
        """

        if self.ccl_win is None:
            self.ccl_win = dock_custom_launcher.DockCLWindow(self.ccl_win_ok_cb)
        else:
            self.ccl_win.set_default_values()

        self.ccl_win.name = self.right_clicked_app.app_name.strip()
        self.ccl_win.wm_class = self.right_clicked_app.wm_class_name
        self.ccl_win.show_all()

    def ccl_win_ok_cb(self, widget, event):
        """ Callback for the 'ok' button on the create custom launcher window.

        Check to ensure that all required fields (icon, launcher name and
        command) have been entered and display an error dialog if not.

        If all required fields have been entered, use the info from the window
        to create a .desktop file in ~/.local/share/applications

        The .desktop file will be named mda_<launcher name>.desktop - the
        initial 'mda_' will allow the applet to search for and priorities self
        created .desktop files over system created ones...

        Args:
            widget - the button the caused the event
            event - the event args
        """

        valid_launcher = False
        if self.ccl_win.name == "":
            error_text = "The name of the launcher has not been set"
        elif self.ccl_win.command == "":
            error_text = "The command of the launcher has not been set"
        elif self.ccl_win.icon_filename == "":
            error_text = "The icon of the launcher has not been set"
        else:
            valid_launcher = True

        if valid_launcher is False:
            md = Gtk.MessageDialog(None, Gtk.DialogFlags.MODAL,
                                   Gtk.MessageType.ERROR,
                                   Gtk.ButtonsType.OK,
                                   None)
            md.set_markup('<span size="x-large"><b>Cannot create launcher</b></span>')
            md.format_secondary_text(error_text)
            md.run()
            md.destroy()
            return

        else:
            self.ccl_win.hide()

            # the gnome developer docs at
            # https://developer.gnome.org/integration-guide/stable/desktop-files.html.en
            # state that .desktop filenames should not contain spaces, so....
            dfname = self.ccl_win.name.replace(" ", "-")

            local_apps = os.path.expanduser("~/.local/share/appplications")
            if not os.path.exists(local_apps):
                # ~/.local/share/applications doesn't exist, so create it
                os.mkdir(local_apps)
            dfname = os.path.expanduser("%s/mda-%s.desktop" % (local_apps, dfname))

            dfile = open(dfname, "w")
            dfile.write("[Desktop Entry]\n")
            dfile.write("Name=%s\n" % self.ccl_win.name)
            dfile.write("Type=Application\n")
            dfile.write("Comment=%s\n" % self.ccl_win.comment)
            dfile.write("Exec=%s\n" % self.ccl_win.command)
            dfile.write("Icon=%s\n" % self.ccl_win.icon_filename)
            dfile.write("StartupWMClass=%s\n" % self.ccl_win.wm_class)

            # Code below can be uncommented if adding terminal apps to the dock
            # everbecomes a needed thing
            # term_app = "%s" %self.ccl_win.is_terminal_app
            # dfile.write("Terminal=%s\n" %term_app.lower())

            # we don't want this launcher displayed in the MATe menu
            dfile.write("NoDisplay=true\n")

            dfile.close()

            # create a docked app from the .desktop we just created and add it
            # to the dock
            dock_app = docked_app.DockedApp()
            dock_app.desktop_file = dfname
            dock_app.read_info_from_desktop_file()
            dock_app.is_pinned = True

            if not config.WITH_GTK3:
                dock_app.applet_win = self.applet.window
            else:
                dock_app.applet_win = self.applet.window.get_window()

            dock_app.applet = self.applet

            dock_app.applet_orient = self.applet.get_orient()
            dock_app.set_indicator(self.indicator)
            dock_app.set_multi_ind(self.multi_ind)
            dock_app.set_active_bg(self.active_bg)
            dock_app.set_attention_type(self.attention_type)

            size = self.applet.get_size()
            self.set_app_icon(dock_app, size)
            self.app_list.append(dock_app)
            self.add_app(dock_app)
            self.show_or_hide_app_icons()
            self.show_or_hide_indicators()
            self.write_settings()

    def add_app_to_dock(self, desktop_file):
        """ Adds the app specified by a desktop file to the dock and pins it

        If the app is already present in the dock, no action is taken

        :param desktop_file: the .desktop_file of the app

        """

        for app in self.app_list:
            if app.desktop_file == desktop_file:
                return

        dock_app = docked_app.DockedApp()
        dock_app.desktop_file = desktop_file
        dock_app.read_info_from_desktop_file()
        if dock_app.read_info_from_desktop_file():
            if not config.WITH_GTK3:
                dock_app.applet_win = self.applet.window
            else:
                dock_app.applet_win = self.applet.get_window()

            dock_app.applet = self.applet
            dock_app.applet_orient = self.applet.get_orient()
            size = self.applet.get_size()
            self.set_app_icon(dock_app, size)
            self.app_list.append(dock_app)
            self.add_app(dock_app)

            if self.show_all_apps:
                dock_app.show_icon()
            else:
                self.show_or_hide_app_icons()
                self.show_or_hide_indicators()

            self.set_all_apps_minimise_targets()

            dock_app.applet_orient = self.applet.get_orient()
            dock_app.set_indicator(self.indicator)
            dock_app.set_multi_ind(self.multi_ind)
            dock_app.set_active_bg(self.active_bg)
            dock_app.set_attention_type(self.attention_type)

            dock_app.is_pinned = True
            self.write_settings()
            return

        if not config.WITH_GTK3:
            dock_app.applet_win = self.applet.window
        else:
            dock_app.applet_win = self.applet.get_window()

        dock_app.applet = self.applet

        dock_app.applet_orient = self.applet.get_orient()
        dock_app.set_indicator(self.indicator)
        dock_app.set_multi_ind(self.multi_ind)
        dock_app.set_active_bg(self.active_bg)
        dock_app.set_attention_type(self.attention_type)

        size = self.applet.get_size()
        self.set_app_icon(dock_app, size)
        self.app_list.append(dock_app)
        self.add_app(dock_app)
        self.show_or_hide_app_icons()
        self.show_or_hide_indicators()
        self.set_all_apps_minimise_targets()
        self.write_settings()

    def show_win(self, win_no):
        """
        Bring the specified window number of the right clicked app to the front

        Args:
            win_no - the window number, starting at 1

        """

        win_list = self.right_clicked_app.get_windows()
        win = win_list[win_no-1]
        window_control.activate(win)

    def close_win(self, data=None):
        """Close all windows for the right clicked app"""

        win_list = self.right_clicked_app.get_windows()
        for win in win_list:
            window_control.close_win(win)

    def icon_theme_changed(self, icontheme):
        """ Callback for when the Gtk icon theme changes

        Load the new icon set
        Iterate through each app in self.app_list and get it to reload it's
        icon
    """

        self.icontheme.rescan_if_needed()

        size = self.applet.get_size()
        for app in self.app_list:
            self.set_app_icon(app, size)

    def find_desktop_file(self, df_name):
        """ Find the full filename of a specified .desktop file

        Search the following directories (and their subdirectories) for
        the specified filename
            /usr/share/applications
            /usr/local/share/applications
            /var/lib/snapd/desktop/applications
            ~/.local/share/applications

        Args :
            df_name : the name of the .desktop file e.g. pluma.desktop. The
                      .desktop extension must be included

        Returns:
            The full filename (path + filename) of the desktop file if it
            exists or "" otherwise
        """

        srch_dirs = ["/usr/share/applications/",
                     "/usr/local/share/applications/",
                     "/var/lib/snapd/desktop/applications/",
                     os.path.expanduser("~/.local/share/applications/")]

        for srch_dir in srch_dirs:
            for the_dir, dir_list, file_list in os.walk(srch_dir):
                try:
                    unused_var = file_list.index(df_name)

                    # if we get here the file is found
                    the_name = os.path.join(the_dir, df_name)
                    return the_name

                except ValueError:
                    pass

        return ""

    def setup_app_list(self):
        """Setup the list of docked apps.

        If pinned apps are pinned to all workspaces read the list of pinned apps from the settings and add them to
        the app list, otherwise get the current workspace and load the config assigned for it, if any

        Then iterate through the running apps, and then either:
            if this is a non-pinned app add it to app list
            if this is a pinned app, integrate the running app info with the
            pinned app details already set up

        Also, set up event handlers allowing us keep track of window added and
        removed events, pluse change of active workspace
        """

        self.wnck_screen.force_update()  # recommended per Wnck documentation

        self.app_list = []
        if self.pa_on_all_ws:
            pinned_apps = self.settings.get_value("pinned-apps").unpack()
            # the settings contain a list of .desktop files, so we need to find and
            # read each file
        else:
            pinned_apps = []
            cur_ws = self.wnck_screen.get_active_workspace()
            if cur_ws is not None:
                ws_name = cur_ws.get_name()
                for config in self.pa_configs:
                    if ws_name == config[1]:
                        for loop in range(2, len(config)):
                            pinned_apps.append(config[loop])

        for pinned_app in pinned_apps:
            dock_app = docked_app.DockedApp()
            full_name = self.find_desktop_file(pinned_app)
            if full_name != "":
                dock_app.desktop_file = full_name
                if dock_app.read_info_from_desktop_file():
                    b_app = self.matcher.get_application_for_desktop_file(full_name, True)
                    dock_app.set_bamf_app(b_app)
                    self.app_list.append(dock_app)

                dock_app.is_pinned = True

        # unpinned apps - get a list of all running apps and if an app is not already in the dock
        # and if it is an app (and not e.g. a panel...) then add it to the dock
        for b_app in self.matcher.get_running_applications():
            if (self.get_docked_app_by_bamf_app(b_app) is None) and b_app.is_user_visible():
                # we need to examine all the app's windows - if any of them are Normal/Dialogs the app needs to be
                # added to the dock
                add_to_dock = False
                for b_win in b_app.get_windows():
                    if (b_win.get_window_type() == Bamf.WindowType.NORMAL) or \
                       (b_win.get_window_type() == Bamf.WindowType.DIALOG) and \
                        b_win.is_user_visible():
                        add_to_dock = True
                        break

                if add_to_dock:
                    dock_app = docked_app.DockedApp()
                    dock_app.set_bamf_app(b_app)

                    dock_app.desktop_file = b_app.get_desktop_file()
                    if dock_app.desktop_file is not None:
                        if dock_app.read_info_from_desktop_file():
                            self.app_list.append(dock_app)
                    else:
                        # bamf cannot match the app, so get as much info about it as we can
                        # e.g. the icon, and use that ...
                        dock_app.setup_from_bamf(self.app_match)
                        self.app_list.append(dock_app)

        # for all the apps we have, setup signal handlers
        for app in self.app_list:
            # connect signal handlers so that we detect windows being added and removed from the Bamf.App
            b_app = app.bamf_app
            self.set_bamf_app_handlers(b_app)

            # for each window the app has open, connect workspace changed events
            for win in app.get_windows():
                win_type = win.get_window_type()
                if (win_type == Bamf.WindowType.NORMAL) or (win_type == Bamf.WindowType.DIALOG):
                    wnck_win = Wnck.Window.get(win.get_xid())
                    if wnck_win is not None:
                        wnck_win.connect("workspace-changed", self.window_ws_changed)

    def clear_dock_apps(self):
        """ Clear out the current list of apps, pinned and unpinned, restoring the
            dock to an empty state
        :return:
        """

        a1 = self.app_list.copy()
        for dock_app in a1:
            self.remove_app_from_dock(dock_app)

    def active_workspace_changed(self, wnck_screen, previously_active_space):
        """ Event handler for the active workspace change even

        Load a saved pinned app config for the new workspace if appropriate

        Show or hide pinned and unpinned dock apps as appropriate

        Arguments :
            wnck_screen : the screen that emitted the event. Will be the same
                          as self.wnck_screen
            previously_active_space : the workspace that was previously active

        """

        # get the name of the new workspace
        ws = wnck_screen.get_active_workspace()
        if ws is None:
            return

        ws_name = ws.get_name()
        update_dock = False

        # if we're using a different dock
        if not self.pa_on_all_ws:
            # we're using different configurations on pinned apps on each workspace
            # so first, clear out the current set of apps
            self.clear_dock_apps()

            # setup the new dock for this workspace
            self.setup_app_list()
            self.setup_dock_apps()
            update_dock = True

        if not self.show_all_apps:
            update_dock = True

        if update_dock:
            self.show_or_hide_app_icons()
            self.show_or_hide_indicators()
            self.set_all_apps_minimise_targets()

    def active_app_changed(self, matcher, object, p0):
        """ Handler of the active app changed signal

        Set the old docked app as inactive, the new one as active and redraw both

        Params:
            matcher: the Bamf.Matcher which received the event - will be the
                     same as self.matcher
            object : the previously active Bamf.Application
            p0     : the new active Bamf.Application
        """

        if object is not None:
            old_app = self. get_docked_app_by_bamf_app(object)
            if old_app is not None:
                old_app.is_active = False
                old_app.queue_draw()

        if p0 is not None:
            new_app = self.get_docked_app_by_bamf_app(p0)
            if new_app is not None:
                new_app.is_active = True
                new_app.queue_draw()

    def set_bamf_app_handlers(self, b_app):
        """ Set up signal handlers for a Bamf.Application

        Params:
            b_app - the Bamf.Application
        """

        if b_app is not None:
            b_app.connect("window-added", self.window_added)
            b_app.connect("window-removed", self.window_removed)
            b_app.connect("running-changed", self.do_running_changed)
            b_app.connect("starting-changed", self.do_starting_changed)
            b_app.connect("urgent-changed", self.do_urgent_changed)

    def remove_bamf_app_handlers(self, b_app):
        """ Remove signal handlers we set up

        Params:
            b_app - the Bamf.Application
        """

        if b_app is not None:
            try:
                b_app.disconnect_by_func(self.window_added)
                b_app.disconnect_by_func(self.window_removed)
                b_app.disconnect_by_func(self.do_running_changed)
                b_app.disconnect_by_func(self.do_starting_changed)
                b_app.disconnect_by_func(self.do_urgent_changed)
            except TypeError:
                pass

    def active_win_changed(self, matcher, object, p0):
        """Event handler for the active window change event

        Remove the highlighted background from any prevously active app and
        redraw its icon

        Set the new app as active and redraw it with a highlighted background

        Args:
            matcher :  the Bamf.Matcher which received the event - will be the
                       same as self.matcher
            object  :  a Bamf.Window - the previously active window
            p0      :  a Bamf Window - the newly active window


        """

        self.wnck_screen.force_update()

        for app in self.app_list:
            if app.is_active is True:
                app.is_active = False
                app.queue_draw()

        if p0 is not None:
            for app in self.app_list:
                if app.has_bamf_window(p0):
                    win_type = p0.get_window_type()

                    # we only want to allow normal and dialog windows to be the last active window
                    if win_type in [Bamf.WindowType.NORMAL, Bamf.WindowType.DIALOG]:
                        app.last_active_win = p0

                    app.is_active = True
                    app.queue_draw()
                    break

    def match_bamf_app_to_dock_app(self, b_app):
        """
            Attempts to match a Bamf.Application to a docked_app

        If a matching docked_app is found, the docked_app will be setup with the
        details from the Bamf app. If a match cannot be found, a new docked app is created
        and added to the dock as an unpinned app

        Params:
            b_app : the Bamf.Application

        Returns: the docked_app that was matched or added to the dock

        """

        # first of all, try to match the application with those in the dock
        # by their .desktop file

        if b_app.get_desktop_file() is not None:
            dock_app = self.get_docked_app_by_desktop_file(b_app.get_desktop_file())
            if (dock_app is not None) and (dock_app.bamf_app is None):
                dock_app.set_bamf_app(b_app)
                self.set_bamf_app_handlers(b_app)
        else:
            # see if there's a match by Bamf.Application
            dock_app = self.get_docked_app_by_bamf_app(b_app)

        if dock_app is None:
            # No match, so add the app to the dock
            dock_app = docked_app.DockedApp()
            dock_app.set_bamf_app(b_app)
            dock_app.desktop_file = b_app.get_desktop_file()

            add_to_dock = True

            if dock_app.desktop_file is not None:
                add_to_dock = dock_app.read_info_from_desktop_file()
            else:
                dock_app.setup_from_bamf(self.app_match)

            if add_to_dock:
                if not config.WITH_GTK3:
                    dock_app.applet_win = self.applet.window
                else:
                    dock_app.applet_win = self.applet.get_window()

                self.set_bamf_app_handlers(b_app)

                dock_app.applet = self.applet
                dock_app.applet_orient = self.applet.get_orient()
                size = self.applet.get_size()
                self.set_app_icon(dock_app, size)
                self.app_list.append(dock_app)
                self.add_app(dock_app, True)
                if self.show_all_apps:
                    dock_app.show_icon()
                else:
                    self.show_or_hide_app_icons()
                    self.show_or_hide_indicators()

                self.set_all_apps_minimise_targets()

                dock_app.applet_orient = self.applet.get_orient()
                dock_app.set_indicator(self.indicator)
                dock_app.set_multi_ind(self.multi_ind)
                dock_app.set_active_bg(self.active_bg)
                dock_app.set_attention_type(self.attention_type)
                self.set_all_apps_minimise_targets()

            else:
                if dock_app.startup_id is not None:
                    # if we have a startup id set this means the dock started the app. Since the app has
                    # now opened a new window we can now assume the app has started and end the notification
                    # process
                    dock_app.cancel_startup_notification()

                    # redraw the app's dock icon
                    dock_app.queue_draw()

        return dock_app

    def view_opened(self, matcher, object):
        """ Handler for the view_opened signal

            If an app has been opened that isn't already in the dock, add it
            If it is already in the dock, add it to the relevant docked_app

        Params: matcher - a Bamf.Matcher
                object -  the Bamf.Application or Bamf.Window that was opened
        """

        self.wnck_screen.force_update()

        if (type(object) is Bamf.Application) and (object.is_user_visible()):
            dock_app = self.match_bamf_app_to_dock_app(object)
            if dock_app is not None:
                if dock_app.startup_id is None:
                    dock_app.pulse_once()

    def window_added(self, application, object):
        """
            Handler for with Bamf.Application window-added signal

        Get the docked_app relating to the Bamf.Application
        If the docked_app is starting and we started it, cancel the startup notification
        Redraw the app icon

        If there isn't a docked app for the Bamf.App, add one to the dock

        Params:
            Application : the Bamf.Application the received the signal
            Object      : the Bamf.Window that has been added

        """

        if (object.get_window_type() not in [Bamf.WindowType.NORMAL, Bamf.WindowType.DIALOG]) or \
             not object.is_user_visible():
            return

        # get the application for the new window
        dock_app = self.get_docked_app_by_bamf_app(application)
        if dock_app is None:
            # try and match it ourselves
            dock_app = self.get_docked_app_by_bamf_window(object)

            if dock_app is None:
                if application.get_desktop_file() is not None:
                    dock_app = self.get_docked_app_by_desktop_file(application.get_desktop_file())

                if dock_app is None:
                    # try again to to match the docked_app, but this time create a new one if it
                    # can't be found
                    dock_app = self.match_bamf_app_to_dock_app(application)

            # connect a signal handler so that we can detect when a window changes workspaces
            win_type = object.get_window_type()
            if (win_type == Bamf.WindowType.NORMAL) or (win_type == Bamf.WindowType.DIALOG):
                wnck_win = Wnck.Window.get(object.get_xid())
                if wnck_win is not None:
                    wnck_win.connect("workspace-changed", self.window_ws_changed)

        # redraw the app's icon to update the number of indicators etc.
        if dock_app is not None:
            if self.show_all_apps:
                dock_app.queue_draw()
            else:
                self.show_or_hide_app_icons()
                self.show_or_hide_indicators()

            # update minimize locations ...
            # at this point, the window will not be returned by application.get_windows() so
            # self.set_minimise_target(dock_app) will not work. Therefore we need to
            # set the minimise target of the new window directly...
            self.set_minimise_target(dock_app, object)

    def window_removed(self, application, object):
        """
            Handler for the Bamf.Application.window-removed signal

        Get the docked app relating to the bamf App
        If the docked app is pinned, redraw it's icon
        If it unpinned, remove the app from the dock if it has no more windows open,
        otherwise redraw the app icon

        Params:
            application - The Bamf.Application that received the signal
            object - the Bamf.Window that is being removed

        """

        dock_app = self.get_docked_app_by_bamf_app(application)
        if dock_app is None:
            dock_app = self.get_docked_app_by_bamf_window(object)
            if dock_app is None:
                df = application.get_desktop_file()

                if df is not None:
                    dock_app = self.get_docked_app_by_desktop_file(df)

        if dock_app is not None:

            # disconnect the signal we connected to the related wnck_window earlier
            wnck_win = Wnck.Window.get(object.get_xid())
            if wnck_win is not None:
                try:
                    wnck_win.disconnect_by_func(self.window_ws_changed)
                except TypeError:
                    pass

            if dock_app.is_pinned:
                dock_app.queue_draw()
            else:
                # note: if the app is no longer running it will be removed in the view_closed handler
                # unpinned apps that are no longer running can be removed from the dock

                if dock_app.is_running():
                    # if the app is still running but does nor have normal or dialog windows remaining
                    # it needs to be removed from the dock
                    keep_in_dock = False
                    for win in dock_app.get_windows():
                        if (win != object) and win.get_window_type() in [Bamf.WindowType.NORMAL,
                                                                         Bamf.WindowType.DIALOG]:
                            keep_in_dock = True
                            break

                    if keep_in_dock:
                        if self.show_all_apps:
                            dock_app.queue_draw()
                        else:
                            self.show_or_hide_app_icons()
                            self.show_or_hide_indicators()
                    else:
                        self.remove_app_from_dock(dock_app)

    def view_closed(self, matcher, object):
        """
            If the object being closed is an app which is not pinned to the dock,
            it needs to be removed from the dock

            If the object is a window then get the associated app and:
                if the app in pinned redraw the dock icom
                if the app is not pinned and is no longer running, remove the app
                from the dock
                if the is not pinned and is still running but has no normal or
                dialog windows open, remove it from the dock, otherwise
                redraw the dock icon

        Params: matcher - a Bamf.Matcher
        object -  the Bamf.Application or Bamf.Window that was closed
        """

        if type(object) is Bamf.Application:

            dock_app = self.get_docked_app_by_bamf_app(object)

            if dock_app is not None:

                if dock_app.startup_id is not None:
                    dock_app.cancel_startup_notification()

                if dock_app.is_pinned:
                    dock_app.is_active = False
                    dock_app.queue_draw()

                elif dock_app.is_running():
                    if self.show_all_apps:
                        dock_app.queue_redraw()
                    else:
                        self.show_or_hide_app_icons()
                        self.show_or_hide_indicators()
                else:
                    self.remove_app_from_dock(dock_app)

                    # to prevent Bamf dbus errors remove signal handlers we added
                    self.remove_bamf_app_handlers(object)

                    self.set_all_apps_minimise_targets()

    def do_urgent_changed(self, view, object):
        """ Handler for the Bamf.Application urgent changed signal

        Update the app's icon to reflect the new urgency state...

        Params:
            view: the object which received the signal (i.e. a Bamf.Application)

            object - bool, whether or not this app is signalling urgency
        """

        dock_app = self.get_docked_app_by_bamf_app(view)

        if dock_app is not None:
            dock_app.set_urgency(object)

    def do_running_changed(self, view, object):
        """ Handler for the Bamf.Application running changed signal

        If the app is pinned, redraw the icon to reflect the change

        Params:
            view: the object which received the signal (i.e. a Bamf.Application)
            object : bool, whether the app is running or not

        """

        dock_app = self.get_docked_app_by_bamf_app(view)

        if dock_app is not None:
            if dock_app.is_pinned:
                dock_app.queue_draw()

    def do_starting_changed(self, view, object):
        """ Handler for the Bamf.Application staring changed signal

       If the related app is starting and the startup_id is not None, then we've
       started the app ourselves so nothing further need be done. If we haven't
       started the app ourselves, make the app icon pulse

       If the app has finished started and we started it ourselves, we need to
       cancel the startup notification process.

       Params:
           view: the object which received the signal (i.e. a Bamf.Application)

           object - bool, whether or not this app is starting
           """

        dock_app = self.get_docked_app_by_bamf_app(view)

        if dock_app is not None:
            if object is True:
                if dock_app.startup_id is None:
                    dock_app.pulse_once()
            else:
                if dock_app.startup_id is not None:
                    dock_app.cancel_startup_notification()

    def window_ws_changed(self, wnck_window):
        """ Handler for the wnck_window workspace changed signal

        If we're showing unpinned apps from the current workspace only, then
        we need to make sure that dock icons are hidden if an unpinned app
        has no more windows on the current workspace, but still has windows
        open on another...

        """

        if not self.show_all_apps:
            self.show_or_hide_app_icons()
            self.show_or_hide_indicators()
            self.set_all_apps_minimise_targets()

    def app_is_pinned_to_workspace(self, app):
        """
            Checks to see if the app is pinned to a specific workspace

        Args:
            app : a docked app

        Returns:
            String: the name of the workspace the app is pinned to, or "" if
                    it isn't pinned to any

        """

        if app.desktop_file is None:
            return""

        df = os.path.basename(app.desktop_file)
        if df is None:
            return ""

        for config in self.pa_configs:

            if df in config or app.desktop_file in self.pa_configs:
                return config[1]

        return ""

    def show_or_hide_app_icons(self):
        """ If we're only showing unpinned apps from the current workspace then
            then show/hide unpinned apps as appropriate.

            If we're showing unpinned apps from all workspaces then we also need
            to check if we're also showing pinned apps on the workspaces they
            were pinned to. If so we can only show icons for apps which are not pinned
            to the current workspace if they're not already pinned to another...

            Finally, recalculate all app minimization targets
        """

        cur_ws = self.wnck_screen.get_active_workspace()

        if self.show_all_apps:
            if self.pa_on_all_ws:
                for app in self.app_list:
                    if not app.is_pinned:
                        if not app.is_visible():
                            app.show_icon()
            else:
                ws_name = cur_ws.get_name()
                for app in self.app_list:
                    if not app.is_pinned:
                        pinned_ws = self.app_is_pinned_to_workspace(app)
                        if pinned_ws == "":
                            app.show_icon()
                        else:
                            app.hide_icon()
        else:
            for app in self.app_list:
                if not app.is_pinned:
                    if app.has_windows_on_workspace(cur_ws):
                        app.show_icon()
                    else:
                        app.hide_icon()

        self.set_all_apps_minimise_targets()

    def show_or_hide_indicators(self):
        """ Show or hide app indicators as appropriate

        If we're only showing indicators for apps which have windows on the
        current workspace then set the apps hide_indicators setting as
        appropriate. Otherwise, set the hide_indicators setting to False.
        """

        cur_ws = self.wnck_screen.get_active_workspace()
        for app in self.app_list:
            if self.win_from_cur_ws_only:
                app.ind_ws = cur_ws
            else:
                app.ind_ws = None

    def remove_app_from_dock(self, app):
        """Remove an app from the dock.

        Remove the app from the app_list

        Remove the app's drawing area from self.box

        Args:
            app : the app to be removed

        """

        app_pos = None
        if config.WITH_GTK3:
            if self.scrolling:
                # clear the scroll indicators
                self.set_app_scroll_dirs(False)
                app_pos = self.get_visible_app_index(app)

        self.app_list.remove(app)

        if config.WITH_GTK3:
            if self.dock_fixed_size == -1:
                self.set_dock_panel_size()

        num_apps = len(self.box.get_children())

        if not config.WITH_GTK3:
            self.box.remove(app.drawing_area)
        else:
            # the row/column which contains the app needs to be
            # removed, so get the app's position in the grid
            pos = 0
            while pos < num_apps:
                if self.box.orientation == Gtk.Orientation.VERTICAL:
                    left = 0
                    top = pos
                else:
                    left = pos
                    top = 0

                if self.box.get_child_at(left, top) == app.drawing_area:
                    # we've found the app
                    if self.box.orientation == Gtk.Orientation.VERTICAL:
                        self.box.remove_row(pos)
                    else:
                        self.box.remove_column(pos)

                pos += 1

            app = None

            # if we're scrolling things get a bit complicated now...
            # (if nice_sizing then we handle things in fit_to_alloc)
            if self.nice_sizing:
                self.ns_app_removed = app_pos
                self.set_size_hints()

            elif self.scrolling:

                # do we now have enough space to show all visible apps?
                if self.get_total_num_visible_apps() < self.dock_fixed_size:
                    self.scrolling = False
                    if self.panel_orient in ["top", "bottom"]:
                        self.scrolled_win.get_hadjustment().set_value(0)
                    else:
                        self.scrolled_win.get_vadjustment().set_value(0)

                else:
                    do_scroll = False
                    if (app_pos is not None) and app_pos < self.scroll_index:

                        do_scroll = True

                    do_scroll = do_scroll or \
                        (self.scroll_index + self.dock_fixed_size >= self.get_total_num_visible_apps())

                    if do_scroll:
                        # if the app we deleted is before the current scroll index, or if we are left with an
                        # empty space at the end of the dock, we need to scroll
                        self.scroll_index -= 1
                        new_pos = self.scroll_index * self.get_app_icon_size()
                        if self.panel_orient in ["top", "bottom"]:
                            self.scrolled_win.get_hadjustment().set_value(new_pos)
                        else:
                            self.scrolled_win.get_vadjustment().set_value(new_pos)

                    self.set_app_scroll_dirs(True)

                #  sort out the app under the mouse
                app = self.get_app_under_mouse()
                self.app_with_mouse = app
                if app is not None:
                    app.has_mouse = True
                    app.queue_draw()

        app = None

    def set_app_icon(self, dock_app, size):
        """ Sets up an app's icon, scaling it to a specified size

        Select an appropriate icon size based on the specified size

        Load the app's icon, using a fallback STOCK_EXEC as a fallback

        Scale the icon to the specified size

        Args:
            dock_app : the DockedApp
            size : the required icon size in pixels
        """

        if size >= 56:
            icon_size = 64
            stock_size = Gtk.IconSize.DIALOG
        elif size >= 40:
            icon_size = 48
            stock_size = Gtk.IconSize.DIALOG
        elif size >= 28:
            icon_size = 32
            stock_size = Gtk.IconSize.DND
        elif size >= 20:
            icon_size = 24
            stock_size = Gtk.IconSize.LARGE_TOOLBAR
        else:
            icon_size = 16
            stock_size = Gtk.IconSize.BUTTON

        dock_app.icon_filename = None

        print ("icon name = %s df = %s" %(dock_app.icon_name, dock_app.desktop_file))

        if dock_app.icon_name is None:
            # can happen e.g. with dock prefs window...
            pixbuf = self.applet.render_icon(Gtk.STOCK_EXECUTE,
                                             stock_size, None)
            dock_app.icon_filename = "STOCK_EXECUTE"

        elif dock_app.has_desktop_file():
            # look up the icon filename using Gtk
            print ("df = %s" %dock_app.desktop_file)
            dai = Gio.DesktopAppInfo.new_from_filename(dock_app.desktop_file)
            the_icon = dai.get_icon()
            if the_icon is Gio.ThemedIcon:
                icon_info = self.icontheme.choose_icon(the_icon.get_names(), icon_size, 0)
            else:
                icon_info = self.icontheme.choose_icon([dock_app.icon_name, None],
                                                       icon_size, 0)
#            self.icontheme.has_icon(dock_app.icon_name):

#
            if icon_info is not None:
                dock_app.icon_filename = icon_info.get_filename()
                print ("icon filename = %s "%dock_app.icon_filename)

                try:
                    pixbuf = icon_info.load_icon()
                except GLib.GError:
                    # default to a stock icon if we couldn't load the app icon
                    pixbuf = self.applet.render_icon(Gtk.STOCK_EXECUTE, stock_size,
                                                None)
                    dock_app.icon_filename = "STOCK_EXECUTE"

        if dock_app.icon_filename is None:
            # the default theme has no icon for the app, so there are a few
            # things we can do...
            #
            # 1 .. quick and dirty - check to see if the icon points to an actual file
            #                        or ...
            #                        look in /usr/share/icons/hicolor/<icon_size>x<iconsize>/
            #                        apps/ and
            #                        ~/.local/share/icons/icons/hicolor</icon_size>x<icon_size>/apps/
            #                        for the icon name or ...
            #                        look in /usr/share/pixmaps for an icon of any type with
            #                        the same name as the app
            #                        then ...
            #                        look in ~/.local/share/icons for an icon with the same name
            #                        and extension as the icon
            #
            # 2 .. sloooow         - iterate through each installed icon theme and try to
            #                        find the app - not implement for now

            # the png method. look for lower and uppercased variations of the filename
            # and note that all files in /usr/share/pixmaps are .png

            icon_file = ""
            if os.path.isfile(dock_app.icon_name):
                pixbuf = GdkPixbuf.Pixbuf.new_from_file(dock_app.icon_name)
            else:
                icon_name = dock_app.icon_name

                # look in the 'hicolor' icon directories for the icon file
                icon_path = "/usr/share/icons/hicolor/%dx%d/apps/" % (icon_size, icon_size)
                if os.path.isfile("%s%s" % (icon_path, icon_name)):
                    icon_file = "%s%s" % (icon_path, icon_name)
                else:
                    icon_path = os.path.expanduser("~/.local/share/icons/hicolor/%dx%d/apps/"
                                                   % (icon_size, icon_size))
                    if os.path.isfile("%s%s" % (icon_path, icon_name)):
                        icon_file = "%s%s" % (icon_path, icon_name)

                # if we still haven't found the icon, look in
                # /usr/share/pixmaps for a .png file
                if icon_file == "":
                    icon_name = os.path.splitext(dock_app.icon_name)[0]  # remove any extension
                    if os.path.isfile("/usr/share/pixmaps/%s.png" % icon_name):
                        icon_file = "/usr/share/pixmaps/%s.png" % icon_name
                    elif os.path.isfile("/usr/share/pixmaps/%s.png" % icon_name.upper()):
                        icon_file = "/usr/share/pixmaps/%s.png" % icon_name.upper()
                    elif os.path.isfile("/usr/share/pixmaps/%s.png" % icon_name.lower()):
                        icon_file = "/usr/share/pixmaps/%s.png" % icon_name.lower()

                # final attempt - look in ~/.local/share/icons for the icon
                if icon_file == "":
                    if os.path.isfile(os.path.expanduser("~/.local/share/icons/%s" % dock_app.icon_name)):
                        icon_file = os.path.expanduser("~/.local/share/icons/%s" % dock_app.icon_name)

                # if we've found an icon, load it
                if icon_file != "":
                        pixbuf = GdkPixbuf.Pixbuf.new_from_file(icon_file)
                else:
                    # if not, use a stock icon to represent the app
                    pixbuf = self.applet.render_icon(Gtk.STOCK_EXECUTE,
                                                     stock_size, None)
                    dock_app.icon_filename = "STOCK_EXECUTE"

        # we should now have an icon - either the app's own icon or the
        # stock_execute ..

        # scale the icon to the size that is available
        # Note - we're allowing for a 3 pixel border all around the icon,
        # hence using size-6
        pixbuf = pixbuf.scale_simple(size - 6, size - 6,
                                     GdkPixbuf.InterpType.BILINEAR)

        dock_app.set_drawing_area_size(size)
        dock_app.set_pixbuf(pixbuf)

    def set_size_hints(self):
        """ Set the size hints for the applet
        """

        app_size = self.get_app_icon_size()

        # Temporary fix https://github.com/mate-desktop/mate-panel/issues/745 to
        # ensure MUTINY layout works as intended
        # TODO: delete this
        if self.panel_layout.upper() == "MUTINY":
            min_icons = self.get_mutiny_fixed_size()
        else:
            min_icons = self.ns_base_apps

        num_vis = self.get_total_num_visible_apps()
        if num_vis > min_icons:
            i = num_vis
            size_hints = []
            while i > 0:
                size_hints.append(i * app_size)
                size_hints.append((i * app_size))  # - 1)
                i -= 1

            size_hints.append(1)
            size_hints.append(0)

            self.applet.set_size_hints(size_hints, min_icons * app_size)

        else:
            self.applet.set_size_hints([num_vis * app_size, 0],
                                       min_icons * app_size)

    def fit_to_alloc(self):
        """ Ensure the dock fits within the space allocated to it on the panel

        Enable/disable scrolling as appropriate

        Scroll newly launched apps into view if necessary

        Remove icons from the dock when apps are closed if necessary

        """

        max_vis = self.get_max_visible_apps()
        total_vis = self.get_total_num_visible_apps()

        if not self.dds_done:
            # we haven't been fully setup yet...
            return

        # TODO: may have do something here to remove scroll arrows when dock is made
        # larger and is no longer scrolling

        if not self.scrolling and (total_vis > max_vis):
            self.enable_app_scrolling()

        if self.scrolling and self.ns_new_app:
            # a new app has been added to the dock, so we need to bring it into view
            self.scroll_index = self.get_total_num_visible_apps() - self.get_max_visible_apps()

            if self.panel_orient in ["top", "bottom"]:
                startpos = self.scrolled_win.get_hadjustment().get_value()
            else:
                startpos = self.scrolled_win.get_vadjustment().get_value()

            endpos = startpos + (self.scroll_index * self.get_app_icon_size())

            ScrollAnimator(self.scrolled_win, startpos, endpos,
                           self.panel_orient, 16, 5, self.scroll_anim_finished_cb)

        elif self.ns_app_removed is not None:
            if self.get_total_num_visible_apps() < max_vis:
                self.scrolling = False
                self.set_app_scroll_dirs(False)
                if self.panel_orient in ["top", "bottom"]:
                    self.scrolled_win.get_hadjustment().set_value(0)
                else:
                    self.scrolled_win.get_vadjustment().set_value(0)
            else:
                do_scroll = False
                if self.ns_app_removed < self.scroll_index:
                    do_scroll = True

                do_scroll = do_scroll or \
                            (self.scroll_index + max_vis >= total_vis)

                if do_scroll:
                    # if the app we deleted is before the current scroll index, or if we are left with an
                    # empty space at the end of the dock, we need to scroll
                    if self.scroll_index != 0:
                        self.scroll_index -= 1

                    new_pos = self.scroll_index * self.get_app_icon_size()
                    if self.panel_orient in ["top", "bottom"]:
                        self.scrolled_win.get_hadjustment().set_value(new_pos)
                    else:
                        self.scrolled_win.get_vadjustment().set_value(new_pos)

                self.set_app_scroll_dirs(True)

        elif self.scrolling:
            # because we have a new allocation we need to redo the scroll indicators
            self.set_app_scroll_dirs(False)
            self.set_app_scroll_dirs(True)

        self.ns_app_removed = None
        self.ns_new_app = False

    def add_app(self, dock_app, do_scroll=False):
        """
        Add an app's drawing area to the VBox/Hbox/Grid container

        Args:
            dock_app : the DockedApp
            do_scroll : boolean - if True indicates that the dock should scroll to bring the
                                  app into view
        """

        if not config.WITH_GTK3:
            self.box.add(dock_app.drawing_area)
        else:
            if not self.nice_sizing:
                # enabling/disabling scolling etc will be handled in fit_to_alloc
                # when nice_sizing
                overflow = False
                if self.dock_fixed_size == -1:
                    self.set_dock_panel_size()
                elif not self.scrolling:
                    overflow = self.will_overflow(self.get_app_icon_size())

                if overflow:
                    self.enable_app_scrolling()
                else:
                    self.set_app_scroll_dirs(False)
            else:
                # we only indicate new apps after delayed setup is done
                # so that we don't scroll the dock during startup
                self.ns_new_app = self.dds_done
                self.set_size_hints()

            pos = len(self.box.get_children())

            # use .attach instead of .add with Gtk.Grid - .add doesn't seem
            # to work properly with vertical orientation...
            if self.box.orientation == Gtk.Orientation.VERTICAL:
                self.box.attach(dock_app.drawing_area, 0, pos, 1, 1)
            else:
                self.box.attach(dock_app.drawing_area, pos, 0, 1, 1)

            if not self.nice_sizing and (self.scrolling and do_scroll):
                # scroll to bring the new app into view
                self.scroll_index = self.get_total_num_visible_apps() - self.dock_fixed_size

                if self.panel_orient in ["top", "bottom"]:
                    startpos = self.scrolled_win.get_hadjustment().get_value()
                else:
                    startpos = self.scrolled_win.get_vadjustment().get_value()

                endpos = startpos + (self.scroll_index * self.get_app_icon_size())

                ScrollAnimator(self.scrolled_win, startpos, endpos,
                               self.panel_orient, 16, 5, self.scroll_anim_finished_cb)

    def get_app_by_pos(self, position):
        """
            Get the app at a specified position in the dock

        :param self:
        :param position: int - the position of the app in self.app_list

        :return: a docked_app, or none if position exceeds the number of
                 apps in the dock

        """

        if position < len(self.app_list):
            return self.app_list[position]
        else:
            return None

    def create_box(self, orientation):
        """Create a vertical or horizontal (depending on the applet orientation)
           box to contain the docked apps areas.

        Args:
            orientation : the applet orientation
        """

        if orientation == MatePanelApplet.AppletOrient.LEFT or \
           orientation == MatePanelApplet.AppletOrient.RIGHT:
            if not config.WITH_GTK3:
                self.box = Gtk.VBox()
            else:
                self.box = Gtk.Grid()
                self.box.orientation = Gtk.Orientation.VERTICAL

        else:
            if not config.WITH_GTK3:
                self.box = Gtk.HBox()
            else:
                self.box = Gtk.Grid()
                self.box.orientation = Gtk.Orientation.HORIZONTAL
                self.box.set_hexpand(False)

        if not config.WITH_GTK3:
            self.box.set_spacing(self.app_spacing + 2)
        else:
            self.box.set_row_spacing(self.app_spacing + 2)
            self.box.set_column_spacing(self.app_spacing + 2)

    def setup_dock(self):
        """Setup the dock."

        Add all applicable pinned apps to the dock

        Add all non-pinned running apps to the dock

        Setup all apps according to the applet size and orientation

        Setup signal handlers

        """

        # make sure the applet is correctly oriented
        orientation = self.applet.get_orient()
        self.create_box(orientation)
        if config.WITH_GTK3:
            self.scrolled_win.add(self.box)

        # setup up pinned and non-pinned running apps
        self.setup_app_list()
        self.setup_dock_apps()
        self.show_or_hide_app_icons()
        self.show_or_hide_indicators()

        # set up signal handlers
        self.matcher.connect("active-window-changed",
                             self.active_win_changed)
        self.matcher.connect("active_application_changed",
                             self.active_app_changed)
        self.matcher.connect("view-opened", self.view_opened)
        self.matcher.connect("view-closed", self.view_closed)

        self.wnck_screen.connect("active-workspace-changed",
                                 self.active_workspace_changed)

    def set_size_request(self):
        """ Set the dock's size request

         Request the 'natural' size of the dock, according to the applet orientation"""

        if not config.WITH_GTK3:
            return

        if self.panel_orient in ["top", "bottom"]:
            self.scrolled_win.set_size_request(-1, self.panel_size)
        else:
            self.scrolled_win.set_size_request(self.panel_size, -1)

    def setup_dock_apps(self):
        """ setup the apps in the app list according to the dock settings
        """
        orientation = self.applet.get_orient()
        applet_size = self.applet.get_size()

        # add the apps to the dock
        for dock_app in self.app_list:
            dock_app.applet_orient = orientation

            dock_app.applet = self.applet

            if not config.WITH_GTK3:
                dock_app.applet_win = self.applet.window
            else:
                dock_app.applet_win = self.applet.get_window()

            dock_app.set_indicator(self.indicator)
            self.set_app_icon(dock_app, applet_size)
            dock_app.set_multi_ind(self.multi_ind)
            dock_app.set_active_bg(self.active_bg)
            dock_app.set_attention_type(self.attention_type)
            self.add_app(dock_app)

        self.set_size_request()

        # make everything visible...
        self.box.show_all()

    def set_new_orientation(self, new_orient):
        """Change the dock applet to a new applet orientation

        For Gtk2:
            Remove all app's drawing areas from the V/HBox

            Remove the V/HBox and create a new one according to the new
            orientation

            Add all of the app's drawing areas to the new V/HBox

        For Gtk3:
            Change the orientation of the Gtk.Grid

            Swap the 'left-attach' and 'top-attach' properties of the grid's
            children

        Args:
            new_orient : the new applet orientation
        """

        # we need to re-read/refresh the panel information ...
        self.get_panel_id()
        self.get_applet_panel_info()

        if not config.WITH_GTK3:
            for dock_app in self.app_list:
                self.box.remove(dock_app.drawing_area)

            self.applet.remove(self.box)
            self.box = None

            self.create_box(new_orient)

            for dock_app in self.app_list:
                self.box.add(dock_app.drawing_area)
                dock_app.applet_orient = new_orient

            self.applet.add(self.box)
        else:
            old_orient = self.box.orientation

            if (new_orient == MatePanelApplet.AppletOrient.RIGHT) or \
               (new_orient == MatePanelApplet.AppletOrient.LEFT):
                self.box.orientation = Gtk.Orientation.VERTICAL
            else:
                self.box.orientation = Gtk.Orientation.HORIZONTAL

            for child in self.box.get_children():
                ol = self.box.child_get_property(child, "left-attach")

                # if we've switched orientations then realign the grid contents
                if old_orient != self.box.orientation:
                    ot = self.box.child_get_property(child, "top-attach")
                    self.box.child_set_property(child, "left-attach", ot)
                    self.box.child_set_property(child, "top-attach", ol)

            for dock_app in self.app_list:
                dock_app.applet_orient = new_orient

    def get_app_at_mouse(self, mouse_x, mouse_y):
        """
        Find the app underneath the mouse cursor.

        Args:
            mouse_x : the x coord of the mouse
            mouse_y : the y coord of the mouse

        Returns:
            The app under the mouse, or None if one could not be found
        """

        for app in self.app_list:
            if app.is_visible():
                alloc = app.drawing_area.get_allocation()

                mx = mouse_x
                my = mouse_y
                if self.scrolling:
                    # if we're scrolling we need to adjust mouse_x (or mouse_y, according to the
                    # panel orientation) to account for the current scroll position

                    if self.panel_orient in ["top", "bottom"]:
                        mx += self.scrolled_win.get_hadjustment().get_value()
                    else:
                        my += self.scrolled_win.get_vadjustment().get_value()
                        # was this ...my += self.scroll_index * self.get_app_icon_size()

                if (mx >= alloc.x) and (mx <= alloc.x + alloc.width):
                    if (my >= alloc.y) and \
                       (my <= alloc.y + alloc.height):
                        return app

        return None

    def reset_scroll_timer(self):
        """ Reset the scroll timer

                If the timer is already instantiated, delete it.

                if the app rhat currently has the mouse has a scroll direction associated
                with it, start another timer
                """

        if self.scroll_timer is not None:
            GObject.source_remove(self.scroll_timer)
            self.scroll_timer = None

        if self.scrolling and (self.app_with_mouse is not None) and \
                              (self.app_with_mouse.scroll_dir != docked_app.ScrollType.SCROLL_NONE):
            self.scroll_timer = GObject.timeout_add(500, self.do_app_scroll)

    def stop_scroll_timer(self):
        """ Stop the win list timer
        """

        if self.scroll_timer is not None:
            GObject.source_remove(self.scroll_timer)
            self.scroll_timer = None

    def do_app_scroll(self):
        """
            Scrolls the dock's scroll window in the direction indicated by the highlighted
            apps
        """

        def hide_popups():
            self.hide_win_list()
            self.stop_act_list_timer()
            self.hide_act_list()

        def set_new_app_with_mouse():
            app = self.get_app_under_mouse()
            app.has_mouse = True
            self.app_with_mouse = app
            app.queue_draw()

        if not self.scrolling:
            # should never happen really...
            return False

        app = self.app_with_mouse

        if app is not None:
            if app.scroll_dir == docked_app.ScrollType.SCROLL_UP:
                if self.scroll_index != 0:
                    self.set_app_scroll_dirs(False)
                    self.scroll_index -= 1

                    # hide popups
                    # the app will no longer have the mouse
                    app.has_mouse = False
                    app.queue_draw()
                    hide_popups()

                    if self.panel_orient in ["top", "bottom"]:
                        sp = self.scrolled_win.get_hadjustment().get_value()
                    else:
                        sp = self.scrolled_win.get_vadjustment().get_value()

                    ScrollAnimator(self.scrolled_win, sp, sp - self.get_app_icon_size(),
                                   self.panel_orient, 16, 5, self.scroll_anim_finished_cb)

                    return False

            elif app.scroll_dir == docked_app.ScrollType.SCROLL_DOWN:
                if self.scroll_index < self.get_total_num_visible_apps():
                    self.set_app_scroll_dirs(False)
                    self.scroll_index += 1

                    app.has_mouse = False
                    app.queue_draw()
                    hide_popups()

                    if self.panel_orient in ["top", "bottom"]:
                        sp = self.scrolled_win.get_hadjustment().get_value()
                    else:
                        sp = self.scrolled_win.get_vadjustment().get_value()

                    ScrollAnimator(self.scrolled_win, sp, sp + self.get_app_icon_size(),
                                   self.panel_orient, 16, 5, self.scroll_anim_finished_cb)

                    return False

        return True

    def scroll_anim_finished_cb(self):
        """ Callback for the scroll animation timer ends

        Add scroll indicators to the dock icons which can now initiate a scroll
        Highlight the app under the mouse
        Reset the scroll timer

        """

        hp = self.scrolled_win.get_vadjustment().get_value()
        self.set_app_scroll_dirs(True)

        # if we're scrolling because the mouse hovered over a docked app, there will be another
        # app under the mouse
        app = self.get_app_under_mouse()
        self.app_with_mouse = app
        if app is not None:
            app.has_mouse = True
            app.queue_draw()

        self.reset_act_list_timer()
        self.scroll_timer = None  # timer will have been cancelled by itself
        self.reset_scroll_timer()
        self.set_all_apps_minimise_targets()

    def reset_act_list_timer(self):
        """ Reset win_list timer

        If the timer is already instantiated, delete it.

        Start a new timer with the appropriate delay

        """

        if self.act_list_timer is not None:
            GObject.source_remove(self.act_list_timer)

        if not self.panel_act_list:
            self.act_list_timer = GObject.timeout_add(self.popup_delay,
                                                      self.show_act_list)

    def stop_act_list_timer(self):
        """ Stop the win list timer
        """

        if self.act_list_timer is not None:
            GObject.source_remove(self.act_list_timer)
            self.act_list_timer = None

    def show_act_list(self):
        """ Show the the list of open windows and actions for the currently
            highlighted app

        If the window list is currently being shown then don't do anything,
        otherwise...

        Get the currently highlighted app. If the highlighted app is being
        launched or a window list is already being displayed for it, or a user
        interaction has already dismissed the window list, then do nothing

        Otherwise, fill the window list, set the window position and set the
        screen areas where the mouse must remain or the window list will hide
        """

        if self.app_win_list.get_visible():
            return

        highlighted_app = self.app_with_mouse

        self.right_clicked_app = highlighted_app
        # above is needed so that actions invoked from the window list work correctly

        if highlighted_app is None:
            return

        # is the app being launched?
        if highlighted_app.is_pulsing:
            self.act_list_timer = None
            return False

        # always recreate the window list e.g. to account for windows being
        # opened/closed, the app being pinned/unpinned etc.

        if self.app_act_list is not None:
            self.app_act_list.destroy()

        self.set_actions_for_app(self.app_with_mouse)

        # first of all, add any shortcut actions specified in the app's
        # .desktop file

        df_shortcut_1_action = self.popup_action_group.get_action("df_shortcut_1_action")
        df_shortcut_2_action = self.popup_action_group.get_action("df_shortcut_2_action")
        df_shortcut_3_action = self.popup_action_group.get_action("df_shortcut_3_action")
        df_shortcut_4_action = self.popup_action_group.get_action("df_shortcut_4_action")
        pin_action = self.popup_action_group.get_action("pin_action")
        unpin_action = self.popup_action_group.get_action("unpin_action")

        # if we're scrolling we need to pass the current scroll position to the
        # action list
        if not self.scrolling or build_gtk2:
            scroll_adj = 0
        else:
            if self.panel_orient in ["top", "bottom"]:
                scroll_adj = self.scrolled_win.get_hadjustment().get_value()
            else:
                scroll_adj = self.scrolled_win.get_vadjustment().get_value()

        self.app_act_list = dock_action_list.DockActionList(self.wnck_screen,
                                                            self.applet.get_orient(),
                                                            scroll_adj)
        self.app_act_list.icontheme = self.icontheme

        # get the panel custom background colour (if any) and then set the
        # window list colours
        self.app_act_list.set_colours(self.get_applet_panel_custom_rgb())

        self.app_act_list.the_app = highlighted_app

        if df_shortcut_1_action.is_visible():
            self.app_act_list.add_to_list(df_shortcut_1_action.get_label(),
                                          df_shortcut_1_action,
                                          True)
            if df_shortcut_2_action.is_visible():
                self.app_act_list.add_to_list(df_shortcut_2_action.get_label(),
                                              df_shortcut_2_action,
                                              True)
            if df_shortcut_3_action.is_visible():
                self.app_act_list.add_to_list(df_shortcut_3_action.get_label(),
                                              df_shortcut_3_action,
                                              True)
            if df_shortcut_4_action.is_visible():
                self.app_act_list.add_to_list(df_shortcut_4_action.get_label(),
                                              df_shortcut_4_action,
                                              True)
            self.app_act_list.add_separator()

        if pin_action.is_visible():
            self.app_act_list.add_to_list(pin_action.get_label(),
                                          pin_action, False)
        if unpin_action.is_visible():
            self.app_act_list.add_to_list(unpin_action.get_label(),
                                          unpin_action, False)

        if self.app_act_list.get_num_rows() == 0:
            self.act_list_timer = None
            return False

        self.app_act_list.clear_mouse_areas()

        applet_x, applet_y = self.get_dock_root_coords()
        applet_w = applet_h = highlighted_app.drawing_area_size

        self.app_act_list.set_applet_details(applet_x, applet_y,
                                             applet_w, applet_h)
        app_x, app_y = self.get_app_root_coords(highlighted_app)
        self.app_act_list.set_app_root_coords(app_x, app_y)

        self.app_act_list.show_all()
        self.app_act_list.set_opacity(0.9)

        self.act_list_timer = None
        return False

    def do_window_selection(self, app):
        """ Allow the user to change an app's currently active window

            If the dock's window list is being used, show or hide it as
            appropriate

            If the thumbnail preview option has been chosen, invoke Compiz via
            dbus

        """

        if self.use_win_list:
            self.show_or_hide_win_list()
        else:

            # get root window id
            if not config.WITH_GTK3:
                # get_xid is non introspectable on gtk2, so use xwininfo
                # instead...
                rw_inf = subprocess.check_output(["xwininfo", "-root"])
                rw_inf = rw_inf.split()
                rwin = int(rw_inf[3], 16)
            else:
                rwin = Gdk.Screen.get_default().get_root_window().get_xid()

            try:
                compiz_service = self.session_bus.get_object('org.freedesktop.compiz',
                                 '/org/freedesktop/compiz/scale/screen0/initiate_key')
                activate = compiz_service.get_dbus_method('activate',
                                                          'org.freedesktop.compiz')
                activate("root", rwin, "match",
                         "class=%s" % app.wm_class_name)
            except dbus.exceptions.DBusException:
                # e.g. Compiz is not installed, or dbus or scale plugin not
                # enabled...
                #
                # fallback to built in window list
                self.show_or_hide_win_list()

    def show_or_hide_win_list(self):
        """
            If the window list is visible, hide it, otherwise show it
        """

        visible = (self.app_win_list is not None) and self.app_win_list.get_visible()
        if visible:
            self.hide_win_list()
        else:
            self.show_win_list()

    def show_win_list(self):
        """ Show the the list of open windows and for the currently highlighted app

        Get the currently highlighted app. If the highlighted app is being
        launched or a window list is already being displayed for it, or a user
        interaction has already dismissed the window list, then do nothing

        Otherwise, fill the window list, set the window position and set the
        screen areas where the mouse must remain or the window list will hide
        """

        if not config.WITH_GTK3:
            highlighted_app = self.app_with_mouse
        else:
            highlighted_app = self.get_app_under_mouse()

        self.right_clicked_app = highlighted_app
        # abive is needed so that actions invoked from the window list work correctly

        if highlighted_app is None:
            return

        # is the app being launched?
        if highlighted_app.is_pulsing:
            self.act_list_timer = None
            return False

        # always recreate the window list e.g. to account for windows being
        # opened/closed

        if self.app_win_list is not None:
            self.app_win_list.destroy()

        self.set_actions_for_app(highlighted_app)

        if not config.WITH_GTK3:
            scroll_adj = 0
        else:
            if self.panel_orient in ["top", "bottom"]:
                scroll_adj = self.scrolled_win.get_hadjustment().get_value()
            else:
                scroll_adj = self.scrolled_win.get_vadjustment().get_value()

        self.app_win_list = dock_win_list.DockWinList(self.wnck_screen,
                                                      self.applet.get_orient(),
                                                      scroll_adj)
        self.app_win_list.icontheme = self.icontheme

        # get the panel custom background colour (if any) and then set the
        # window list colours
        self.app_win_list.set_colours(self.get_applet_panel_custom_rgb())

        self.app_win_list.the_app = highlighted_app

        # add any open windows
        if highlighted_app.is_running():
            self.app_win_list.setup_list(self.win_from_cur_ws_only)

        self.app_win_list.clear_mouse_areas()

        applet_x, applet_y = self.get_dock_root_coords()
        applet_w = applet_h = highlighted_app.drawing_area_size

        self.app_win_list.set_applet_details(applet_x, applet_y,
                                             applet_w, applet_h)
        app_x, app_y = self.get_app_root_coords(highlighted_app)
        self.app_win_list.set_app_root_coords(app_x, app_y)

        self.app_win_list.show_all()
        self.app_win_list.set_opacity(0.9)

        self.act_list_timer = None
        return False

    def hide_win_list(self):
        """ Hide the window list """

        if self.app_win_list is not None:
            self.app_win_list.hide()

    def hide_act_list(self):
        """ Hide the action list """

        if self.app_act_list is not None:
            self.app_act_list.hide()

    def minimize_or_restore_windows(self, app, event):
        """ Minimize or restore an app's windows

        the action to perform (minimizing, moving workspace, activating)
        is decided as follows:
           if (the app's windows are all minimized) or
           (the app has one or more unminimized window but is not the active app)
           then
               restore the app's last active window or all windows (based on
               the user's settings). If the active window is on a  different
               workspace then activate that workspace
           else:
               the app is currently the active app so all of the app
               windows will be minimized

        but first, hide any app window list that is being shown and stop the
        window list timer

        Note: As of MATE 1.12 wnck_window.activate does not seem to work
              if we specify our own event time. However, if an event time of
              0 (i.e. now) is specfied all works as expected. However, when the
              applet is run from the command line, lots of these messages
              'Wnck-WARNING **: Received a timestamp of 0; window activation
               may not function properly' appear, so another solution may need
              to be found in the future

        Args:
            app: the docked app whose windows are to be minimized or restored
            event : the mouse click event

        """

        self.stop_act_list_timer()
        self.hide_win_list()

        restore_win = (not app.has_unminimized_windows()) or \
                      (app.has_unminimized_windows() and
                       (app.is_active is False))

        last_active_win = None
        if restore_win:
            last_active_win = app.last_active_win

            # the last active window may be set to None (e.g. if the app's
            # active window has been closed and no other window has been made
            # active afterwards). Therefore, if there is no active last window
            # activate the app's first normal window
            # (related to https://bugs.launchpad.net/ubuntu/+source/mate-dock-applet/+bug/1550392)
            if last_active_win is None:
                last_active_win = app.get_first_normal_win()

            # if we're restoring all windows, do this now before we finally
            # activate the last active window
            if self.click_restore_last_active is False:
                for win in app.get_windows():
                    win_type = win.get_window_type()
                    if (win_type in [Bamf.WindowType.NORMAL, Bamf.WindowType.DIALOG] or
                        win.is_user_visible()) and (win != last_active_win):
                        window_control.activate_win(win)
                        sleep(0.01)

                app.last_active_win = last_active_win

            if last_active_win is not None:
                wnck_win = Wnck.Window.get(last_active_win.get_xid())
                if wnck_win is not None:
                    wnck_aws = self.wnck_screen.get_active_workspace()
                    wnck_ws = wnck_win.get_workspace()

                    # the window's active workspace can be None if it is visible on
                    # all workspaces or if it is not on any workspace (I'm looking at
                    # you caja-desktop!!!!!)
                    # (fix for https://bugs.launchpad.net/ubuntu/+source/mate-dock-applet/+bug/1550392 and
                    # https://bugs.launchpad.net/ubuntu-mate/+bug/1555336 (regarding software updater))
                    if wnck_aws is not None and wnck_ws is not None and \
                       (wnck_aws != wnck_ws):
                        wnck_ws.activate(0)
                        sleep(0.01)

            # rarely, the last active win does not end up as the active window
            # if we activate here, so instead a workaround which seems to do
            # the trick is use a timer as below
            if event is None:
                # use the current time if we have no event
                event_time = Gtk.get_current_event_time()
            else:
                event_time = event.time

            GObject.timeout_add(20, win_activation_timer,
                                [last_active_win, event_time])

        else:
            # minimize all windows and do the last active window last of all

            last_active_win = app.last_active_win
            for win in app.get_windows():
                win_type = win.get_window_type()
                if (win_type in [Bamf.WindowType.NORMAL, Bamf.WindowType.DIALOG] or
                    win.is_user_visible()) and (win != last_active_win):
                    window_control.minimise_win(win)
                    sleep(0.01)

            app.last_active_win = last_active_win

            if last_active_win is not None:
                window_control.minimise_win(last_active_win)
                sleep(0.01)

    def activate_window(self, win):
        """ Activate a Bamf window, switching workspace as necessary

            Args:
                win : the Bamf.Window
        """
        if win is not None:
            # if the window to be activated is not on the current workspace,
            # switchto that workspace
            wnck_win = Wnck.Window.get(win.get_xid())
            wnck_aws = self.wnck_screen.get_active_workspace()
            wnck_ws = wnck_win.get_workspace()

            # the windows's current workspace can be None if it is pinned to all
            # workspaces or it is not on any at all...
            # (fix for https://bugs.launchpad.net/ubuntu/+source/mate-dock-applet/+bug/1550392 and
            # https://bugs.launchpad.net/ubuntu-mate/+bug/1555336 (regarding software updater))
            if (wnck_aws is not None) and (wnck_ws is not None) and \
               (wnck_aws != wnck_ws):
                    wnck_ws.activate(0)
                    sleep(0.01)

            window_control.activate(win)

    def activate_first_window(self, app):
        """ Active the specified apps's first window, changing workspace as
            necessary
        """

        if app is None:
            return

        win = app.get_first_window()
        if win is None:
            return
        self.activate_window(win)

    def do_window_scroll(self, scroll_dir, event_time, the_app=None):
        """ Scroll to the next/previous window of the currently active app

        This function is called in response to the mouse scroll event on the
        panel applet and also when an applet keyboard shortcut (e.g. <Super>1)
        is used

        Depending on the scroll direction, make the next or previous window of
        the current app active. Scrolling will wrap around in both directions

        If the app only has one window or we don't know which window was last
        active (e.g. because the applet has only just started) then make the
        first window active

        If the new window is not on the current workspace, change to the
        relevant workspace

        Also, hide the app window list and stop any timer that might be running

        Args:
            scroll_dir : A GDK.ScrollDirection which indicates whether to go
                         forwards or backwards through the window list
            event_time : the time scroll event occurred
            the_app    : will indicate the app whose windows are to be scrolled
                         when a keyboard shortcut has been used.
        """

        if (scroll_dir != Gdk.ScrollDirection.UP) and \
           (scroll_dir != Gdk.ScrollDirection.DOWN):
            return

        if the_app is None:
            # we've been called in response to a mouse scroll event, so we need to get
            # the app under the mouse
            app = self.get_app_under_mouse()
#            if self.app_with_mouse is not None:
#                app = self.app_with_mouse
#            else:
#                return
        else:
            app = the_app

        # if the app isn't running, there's nothing to do...
        if app.is_running() is False:
            return

        windows = app.get_windows()
        if (app.last_active_win is None) or (len(windows) == 1):
            new_index = 0
        else:
            # work out which window we want to activate
            if app.last_active_win in windows:
                index = windows.index(app.last_active_win)
            else:
                index = 0  # in case of error activate the first window

            if scroll_dir == Gdk.ScrollDirection.UP:
                if index == 0:
                    new_index = len(windows)-1
                else:
                    new_index = index - 1
            else:
                if index == len(windows)-1:
                    new_index = 0
                else:
                    new_index = index + 1

        wnck_win = Wnck.Window.get(windows[new_index].get_xid())
        # hide the window list and stop any timer
        self.hide_win_list()
        self.stop_act_list_timer()

        # if the new window is on a different workspace, we need to switch
        # workspace

        wnck_aws = self.wnck_screen.get_active_workspace()
        wnck_ws = wnck_win.get_workspace()

        if wnck_aws is not None and (wnck_aws != wnck_ws):
            wnck_ws.activate(0)
            sleep(0.01)

        # activate the new window
        window_control.activate_win(windows[new_index])

    def get_dragee(self):
        """"
            Return the app which is currently marked as being dragged to a new
            position in the dock.

        Returns:
            a docked_app, or None is no apps are being dragged

        """

        for app in self.app_list:
            if app.is_dragee is True:
                return app

        return None

    def start_drag_motion_timer(self, dragee):
        """ Create a timer to allow us to monitor the mouse position during
            the drag/drop and rearrange dock icons on the fly

        Args: dragee - the docked_app which is being dragged

        """

        self.dm_timer = DragMotionTimer(dragee, self)

    def stop_drag_motion_timer(self):
        """  Stop the drag motion timer
        """

        self.dm_timer.drag_ended = True

    def start_da_timer(self, app):
        """
            Use a timer to before activating an app

        Args:
            app : the app to activate
        """

        # first of all, stop any other timer da_timer that may be running
        if (self.da_timer is not None) and (self.da_timer.timer_id != 0):
            GObject.source_remove(self.da_timer.timer_id)

        # create a new timer
        self.da_timer = DragActivateTimer(self, app)

    def window_scroll(self, widget, event):

        self.scrolled_win.emit_stop_by_name("scroll-event")
        return False

    def get_app_under_mouse(self):
        """ Get the docked which is currently under the mouse cursor

        Returns : a docked_app, or None if the cursor is not over a docked_app
        """

        # get the mouse device so we can obtain the root coordinates of the
        # the cursor
        display = Gdk.Display.get_default()
        manager = display.get_device_manager()
        mouse = manager.get_client_pointer()
        none, x, y = mouse.get_position()
        dx, dy = self.get_dock_root_coords()

        # convert to applet coords
        x = x - dx
        y = y - dy

        return self.get_app_at_mouse(x, y)

    def get_avail_panel_space(self):
        """ Gets the amount of space (w and h) that is available to the dock on the
            the panel

            Needs to be called after get_applet_panel_info

            Returns:
                  Two ints, the required width and the height in pixels

        """

        if self.panel_orient in ["top", "bottom"]:
            # horizontal panel ...
            return self.dock_fixed_size * self.get_app_icon_size(), self.panel_size
        else:
            return self.panel_size, self.dock_fixed_size * self.get_app_icon_size()

    def get_total_num_visible_apps(self):
        """
            Get the total number of dock apps which are visible

        Returns: int
        """

        num_vis = 0
        for app in self.app_list:
            if app.is_visible:
                num_vis += 1

        return num_vis

    def get_visible_app(self, app_no):
        """
            Gets a visible app in the dock

        Params:
            app_no : the number of the visible docked app to get (0 = the first, 1 = the second etc.)

        Returns : a docked app, or None if e.g. app_no exceeds the number of visible apps

        """

        count = 0
        for app in self.app_list:
            if app.is_visible:
                if count == app_no:
                    return app
                else:
                    count += 1

        return None

    def get_visible_app_index(self, vis_app):
        """
            Get the index of the specified visible app in a list of all visible apps

        Param: vis_app : the docked app in question

        Returns : an int (the index) or None of the app could not be found
        """

        vis_list = []
        for app in self.app_list:
            if app.is_visible():
                vis_list.append(app)

        if vis_app in vis_list:
            return vis_list.index(vis_app)
        else:
            return None

    def get_mutiny_fixed_size(self, icon_size=True):
        """ Temporary fix for sizing the dock in the Mutiny layout

        Called when the mutiny layout is being used and calculates either the fixed
        number of icons that can be displayed in the dock, or the panel size
        available to the dock before scrolling starts, based on the known size of
        the Mutiny panels and their applets

        Params:
            icon_size : whether to return the result as the number of icons, or as
                        a size in pixels

        Returns : int - the maximum number of icons that can be displayed in the dock,
                        or a panel size in pixels"""

        brisk_h = 48
        top_panel_h = 28
        trash_h = 48

        result = (Gdk.Screen.get_default().get_height() - top_panel_h - brisk_h - trash_h)
        if icon_size:
            return result // self.get_app_icon_size()
        else:
            return result

    def set_dock_panel_size(self):
        """ Adjust the size of the dock to prevent it overlapping or expanding over
            other applets. Also, ensure that the size is set such that any partially
            visible dock icons at the end of the dock are not shown"""

        # get the number of icons which can be displayed
        num_icons = self.dock_fixed_size
        app_icon_size = self.get_app_icon_size()

        num_vis = self.get_total_num_visible_apps()
        if num_icons == -1:
            # we want to size the dock to the number of visible apps
            ps = num_vis * app_icon_size
        elif num_vis > num_icons:
            # we need to start scrolling
            ps = num_icons * app_icon_size

            self.scrolling = True
            self.enable_app_scrolling()

        else:
            # there's no need for scrolling right now
            ps = num_icons * app_icon_size
            if self.scrolling:
                self.scrolling = False
                self.set_app_scroll_dirs(False)

        alloc = self.applet.get_allocation()

        # Scale panel size to accomodate dock on HiDPI displays
        scale_factor = self.box.get_scale_factor()
        panel_size = int(self.panel_size / scale_factor)

        if self.panel_orient in ["top", "bottom"]:

            # first set the min content width to 0 in case the new maximum we're going
            # to set is less the current minimim (nastiness can occur...)
            self.scrolled_win.set_min_content_width(0)
            self.scrolled_win.set_max_content_width(ps)
            self.scrolled_win.set_min_content_width(ps)

            self.scrolled_win.set_min_content_height(0)
            self.scrolled_win.set_max_content_height(panel_size)
            self.scrolled_win.set_min_content_height(panel_size)

        else:
            self.scrolled_win.set_min_content_height(0)
            self.scrolled_win.set_max_content_height(ps)
            self.scrolled_win.set_min_content_height(ps)

            self.scrolled_win.set_min_content_width(0)
            self.scrolled_win.set_max_content_width(panel_size)
            self.scrolled_win.set_min_content_width(panel_size)

    def get_max_visible_apps(self):
        """ Gets the maximum number of whole app icons the dock can display in the available
            panel space

        Returns: int """

        if not self.nice_sizing:
            if self.avail_panel_space == (1, 1):
                # can happen during applet startup
                return 1

            pw, ph = self.avail_panel_space
        else:
            alloc = self.applet.get_allocation()
            pw = alloc.width
            ph = alloc.height

        app_size = self.get_app_icon_size()
        if self.panel_orient in ["top", "bottom"]:
            return pw // app_size
        else:
            return ph // app_size

    def adjust_minimise_pos(self, x, y):
        """
            Adjust the x and y minimise coordinate so that it takes account of the current scroll
            position

            Ensure that windows related to dock icons that have been scrolled off the dock minimise
            to the relevant end of the dock and not beyond. Windows relating to visible dock icons
            should minimise to their icons

        Returns: two ints, the x and y of the top left corner where the dock icon should minimise
                 to
        """

        if not self.scrolling:
            return x, y   # no adjustment necessary

        dx, dy = self.get_dock_root_coords()

        if self.panel_orient in ["top", "bottom"]:
            final_x = x - self.scrolled_win.get_hadjustment().get_value()
            if self.nice_sizing:
                max_w = self.applet.get_allocation().width
            else:
                max_w = self.scrolled_win.get_max_content_width()
            final_x = min(max(final_x, dx), dx + max_w)
            final_y = y
        else:
            final_y = y - self.scrolled_win.get_vadjustment().get_value()
            if self.nice_sizing:
                max_h = self.applet.get_allocation().height
            else:
                max_h = self.scrolled_win.get_max_content_height()
            final_y = min(max(final_y, dy), dy + max_h)
            final_x = x

        return final_x, final_y

    def get_app_icon_size(self):
        """ Gets the size of a single app icon

        Takes account of the row/column spacing in self.box and, if the panel is horizontal,
        the extra space (if any) required by the current indicator
        Returns : int - the size in pixels

        """

        if self.panel_orient in ["top", "bottom"]:
            return self.panel_size + docked_app_helpers.ind_extra_s(self.indicator) + \
                   self.box.get_column_spacing()
        else:
            return self.panel_size + docked_app_helpers.ind_extra_s(self.indicator) + \
                   self.box.get_row_spacing()

    def enable_app_scrolling(self):
        """
            Enables scrolling of docked apps

        """

        self.scrolling = True
        self.scroll_index = 0

        self.set_app_scroll_dirs(True)      # make sure the appropriate app icons will indicate
                                            # to the user that they can scroll

    def reset_scroll_position(self):
        """ Reset the scroll position back to the start of the dock
        """

        self.scroll_index = 0
        if self.panel_orient in ["top", "bottom"]:
            self.scrolled_win.get_hadjustment().set_value(0)
        else:
            self.scrolled_win.get_vadjustment().set_value(0)

    def set_app_scroll_dirs(self, can_scroll):
        """
            Set the scroll_dir field of the first and last visible dock app

            If can_scroll is True the fields will be set to indicate scrolling
            can occur in the appropriate direction. If can_scroll is False, the
            fields will be set to SCROLL_NONE.

        Param : can_scroll - bool
        """

        da1 = None
        da2 = None

        if self.nice_sizing and (not can_scroll):
            # check the scroll direction on all apps and set clear where necessary
            for app in self.app_list:
                if app.scroll_dir != docked_app.ScrollType.SCROLL_NONE:
                    app.set_scroll_dir(docked_app.ScrollType.SCROLL_NONE)
                    app.queue_draw()
            return

        if self.scroll_index != 0:
            da1 = self.get_visible_app(self.scroll_index)

        if not self.nice_sizing and (self.dock_fixed_size < 2):
            return

        if self.nice_sizing:
            max_vis = self.get_max_visible_apps()
        else:
            max_vis = self.dock_fixed_size

        if self.scroll_index + max_vis <= self.get_total_num_visible_apps()-1:
            da2 = self.get_visible_app(self.scroll_index + max_vis-1)

        if not can_scroll:
            if da1 is not None:
                da1.set_scroll_dir(docked_app.ScrollType.SCROLL_NONE)
            if da2 is not None:
                da2.set_scroll_dir(docked_app.ScrollType.SCROLL_NONE)
        else:
            if da1 is not None:
                da1.set_scroll_dir(docked_app.ScrollType.SCROLL_UP)
            if da2 is not None:
                da2.set_scroll_dir(docked_app.ScrollType.SCROLL_DOWN)

        if da1 is not None:
            da1.queue_draw()
        if da2 is not None:
            da2.queue_draw()

    def will_overflow(self, extra_space):
        """  Check to see if the applet will exceed the amount of space allocated to it on
             the panel if the specified extra space is allocated to it

        Args: extra_space : the extra space - will typically be the size of an app icon plus spacing
                            specified by self.box and the current indicator type
        Returns:
            bool
        """

        if self.avail_panel_space == ():
            # can happen during applet startup, so in this case just return false
            return False

        avail_w, avail_h = self.avail_panel_space
        if (avail_w <= 1) or (avail_h <= 1):
            return False

        alloc = self.applet.get_allocation()

        if self.applet.get_orient() in [MatePanelApplet.AppletOrient.UP,
                                        MatePanelApplet.AppletOrient.DOWN]:
            return (alloc.width + extra_space) > avail_w
        else:
            return (alloc.height + extra_space) > avail_h

    def unity_cb_handler(self, app_uri, args):
        """ Handler for Unity API dbus messages

        If the specified app is in the dock, forward the set the progress
        and/or count, and redraw the app's icon

        Args:
            app_uri : the basename of the .desktop file of the app
            args : the contents of the dbus message

        """

        # remove the leading part of the app uri
        df = app_uri.split("://")[1]

        # search for the an app which has the same desktop file name
        for app in self.app_list:
            app_df_path, app_df = os.path.split(app.desktop_file)
            if app_df == df:
                # we've found the app - update it...

                if "count-visible" in args:
                    app.set_counter_visible(args["count-visible"])
                if "count" in args:
                    app.set_counter_value(args["count"])

                if "progress-visible" in args:
                    app.set_progress_visible(args["progress-visible"])
                if "progress" in args:
                    app.set_progress_value(args["progress"])
                break

    # TODO: could do with being a propert
    def get_drag_coords(self):
        return self.drag_x, self.drag_y

    def set_drag_coords (self, x, y):
        self.drag_x = x
        self.drag_y = y

    def clear_drag_coords (self):
        self.drag_x = self.drag_y = -1


def win_activation_timer(args):
    """ Timer function to be called by GObject.timeout_add and which
        will activate a specified window

    Args:
        args - a tuple containing these items
               args[0] - the Bamf.Window to activate
               args[1] - the event time at which the timer was activated

    Returns:
        False - to cancel the timer
    """

    # as of MATE 1.12 it seems we need to use an event time of 0 (i.e. now)
    # to the window to activate properly
    window_control.activate_win(args[0])
    sleep(0.01)
    return False
