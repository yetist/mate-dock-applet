appletdir = $(libdir)/mate-applets/mate-dock-applet/
applet_PYTHON = \
		     dock_applet.py \
		     dock_about.py \
		     dock_prefs.py \
		     dock.py \
		     docked_app.py \
		     dock_custom_launcher.py \
		     log_it.py \
		     dock_win_list.py \
		     dock_action_list.py \
		     dock_xml.py \
		     dock_color_changer.py \
		     dom_color.py \
		     dock_popup.py \
		     dock_info.py \
		     docked_app_helpers.py \
		     window_control.py

appletsdir       = $(datadir)/mate-panel/applets
applets_in_files = org.mate.panel.DockApplet.mate-panel-applet.in
applets_DATA     = $(applets_in_files:.mate-panel-applet.in=.mate-panel-applet)
nodist_applet_PYTHON = config.py

$(applets_DATA): $(applets_in_files) Makefile
	$(AM_V_GEN)sed -e "s|\@LOCATION\@|$(appletdir)|" $< > $@

config.py: config.py.in Makefile
	$(AM_V_GEN)sed					\
		-e s!\@prefix\@!$(prefix)!		\
		-e s!\@datadir\@!$(datadir)!		\
		-e s!\@pkgdatadir\@!$(pkgdatadir)!	\
		-e s!\@libexecdir\@!$(libexecdir)!	\
		-e s!\@pythondir\@!$(pythondir)!	\
		-e s!\@libdir\@!$(libdir)!		\
		-e s!\@PACKAGE\@!$(PACKAGE)!		\
		-e s!\@VERSION\@!$(VERSION)!		\
		-e s!\@GETTEXT_PACKAGE\@!$(GETTEXT_PACKAGE)!	\
		-e s!\@WITH_GTK3\@!$(WITH_GTK3)!		\
		< $< > $@

servicedir       = $(datadir)/dbus-1/services
service_in_files = org.mate.panel.applet.DockAppletFactory.service.in
service_DATA     = $(service_in_files:.service.in=.service)

$(service_DATA): $(service_in_files) Makefile
	$(AM_V_GEN)sed -e "s|\@LOCATION\@|$(appletdir)dock_applet.py|" $< > $@

xmldir		 =$(appletdir)
xml_DATA 	 = app_match.xml

@INTLTOOL_XML_RULE@

@PANEL_INTLTOOL_MATE_PANEL_APPLET_RULE@

@GSETTINGS_RULES@

dock_gschemas_in = org.mate.panel.applet.dock.gschema.xml.in
gsettings_SCHEMAS = $(dock_gschemas_in:.xml.in=.xml)

CLEANFILES = \
	     $(service_DATA)	\
	     $(applets_DATA)	\
	     $(gsettings_SCHEMAS) \
	     config.py

EXTRA_DIST = $(gsettings_SCHEMAS) \
	     $(applets_in_files) \
	     $(service_in_files) \
	     $(dock_gschemas_in) \
	     $(xml_DATA) \
	     config.py.in

all-local: config.py
