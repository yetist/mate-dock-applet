AC_INIT([Dock Applet], [0.85])

AM_INIT_AUTOMAKE
AM_PATH_PYTHON([3.0])
AM_MAINTAINER_MODE
m4_ifdef([AM_SILENT_RULES],[AM_SILENT_RULES([yes])])

GLIB_GSETTINGS

AC_ARG_WITH([gtk3],
    [AS_HELP_STRING([--with-gtk3], [Build for Gtk3 instead of Gtk2])],
    [],
    [with_gtk3=no])
if test "x$with_gtk3" != "xno"; then
	WITH_GTK3=True
else
	WITH_GTK3=False
fi
AC_SUBST(WITH_GTK3)

dnl ================================================================
dnl  Language Support
dnl ================================================================
GETTEXT_PACKAGE=dock-applet
AC_DEFINE_UNQUOTED([GETTEXT_PACKAGE], ["$GETEX_PACKAGE"], [Define the gettext package to be used])
AC_SUBST(GETTEXT_PACKAGE)
AM_GLIB_GNU_GETTEXT
IT_PROG_INTLTOOL([0.40.0])

m4_pattern_allow([AM_V_GEN])dnl Make autoconf not complain about the rule below
PANEL_INTLTOOL_MATE_PANEL_APPLET_RULE='%.mate-panel-applet: %.mate-panel-applet.in $(INTLTOOL_MERGE) $(wildcard $(top_srcdir)/po/*.po) ; $(INTLTOOL_V_MERGE)LC_ALL=C $(INTLTOOL_MERGE) $(INTLTOOL_V_MERGE_OPTIONS) -d -u -c $(top_builddir)/po/.intltool-merge-cache $(top_srcdir)/po $< [$]@'
AC_SUBST([PANEL_INTLTOOL_MATE_PANEL_APPLET_RULE])


#AC_CONFIG_FILES([Makefile  src/Makefile po/Makefile.in])
AC_CONFIG_FILES([
Makefile
po/Makefile.in
src/org.mate.panel.applet.dock.gschema.xml
src/Makefile
])


AC_OUTPUT
