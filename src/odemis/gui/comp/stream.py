# -*- coding: utf-8 -*-
"""
:author: Rinze de Laat <laat@delmic.com>
:copyright: © 2012 Rinze de Laat, Delmic

This file is part of Odemis.

.. license::
    Odemis is free software: you can redistribute it and/or modify it under the terms of the GNU
    General Public License version 2 as published by the Free Software Foundation.

    Odemis is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY; without
    even the implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the GNU
    General Public License for more details.

    You should have received a copy of the GNU General Public License along with Odemis. If not,
    see http://www.gnu.org/licenses/.

This module contains classes needed to construct stream panels.

Stream panels are custom, specialized controls that allow the user to view and manipulate various
data streams coming from the microscope.

"""

from __future__ import division

from decorator import decorator
import logging
from odemis import acq
from odemis.gui import FG_COLOUR_EDIT, FG_COLOUR_MAIN, BG_COLOUR_MAIN, BG_COLOUR_STREAM, \
    FG_COLOUR_DIS
from odemis.gui import img
from odemis.gui.comp.combo import ComboBox
from odemis.gui.comp.foldpanelbar import FoldPanelItem, FoldPanelBar
from odemis.gui.comp.radio import GraphicalRadioButtonControl
from odemis.gui.comp.slider import UnitFloatSlider, VisualRangeSlider, UnitIntegerSlider, Slider
from odemis.gui.comp.text import SuggestTextCtrl, UnitFloatCtrl, FloatTextCtrl, UnitIntegerCtrl
from odemis.gui.util import call_in_wx_main
from odemis.gui.util.widgets import VigilantAttributeConnector
import wx
import wx.lib.newevent
from wx.lib.pubsub import pub

import odemis.gui as gui
import odemis.gui.comp.buttons as buttons


stream_remove_event, EVT_STREAM_REMOVE = wx.lib.newevent.NewEvent()
stream_visible_event, EVT_STREAM_VISIBLE = wx.lib.newevent.NewEvent()
stream_peak_event, EVT_STREAM_PEAK = wx.lib.newevent.NewEvent()

# Values to control which option is available
OPT_NAME_EDIT = 1  # allow the renaming of the stream (for one time only)
OPT_BTN_REMOVE = 2  # remove the stream entry
OPT_BTN_SHOW = 4  # show/hide the stream image
OPT_BTN_UPDATE = 8  # update/stop the stream acquisition
OPT_BTN_TINT = 16  # tint of the stream (if the VA exists)
OPT_BTN_PEAK = 32  # show/hide the peak fitting data

CAPTION_PADDING_RIGHT = 5
ICON_WIDTH, ICON_HEIGHT = 16, 16


@decorator
def control_bookkeeper(f, self, *args, **kwargs):
    """ Clear the default message, if needed, and advance the row count """
    result = f(self, *args, **kwargs)

    # This makes the 2nd column's width variable
    if not self.gb_sizer.IsColGrowable(1):
        self.gb_sizer.AddGrowableCol(1)

    # Redo FoldPanelBar layout
    win = self
    while not isinstance(win, FoldPanelBar):
        win = win.Parent
    win.Layout()
    self.num_rows += 1
    return result


class StreamPanelHeader(wx.Control):
    """ This class describes a clickable control responsible for expanding and collapsing the
    StreamPanel to which it belongs.

    It can also contain various sub buttons that allow for stream manipulation.

    """

    BUTTON_SIZE = (18, 18)  # The pixel size of the button
    BUTTON_BORDER_SIZE = 9  # Border space around the buttons

    def __init__(self, parent, wid=wx.ID_ANY, pos=wx.DefaultPosition, size=wx.DefaultSize,
                 style=wx.NO_BORDER):
        assert(isinstance(parent, StreamPanel))
        super(StreamPanelHeader, self).__init__(parent, wid, pos, size, style)

        self.SetBackgroundColour(self.Parent.BackgroundColour)

        # This style enables us to draw the background with our own paint event handler
        self.SetBackgroundStyle(wx.BG_STYLE_PAINT)

        # Callback when the label changes: (string (text) -> None)
        self.label_change_callback = None

        # Create and add sizer and populate with controls
        self._sz = wx.BoxSizer(wx.HORIZONTAL)

        # Fold indicator icon, drawn directly in the background in a fixed position
        self._foldIcons = wx.ImageList(16, 16)
        self._foldIcons.Add(img.getBitmap("icon/arr_down_s.png"))
        self._foldIcons.Add(img.getBitmap("icon/arr_right_s.png"))

        # Add the needed controls to the sizer

        self.btn_remove = self._add_remove_btn() if self.Parent.options & OPT_BTN_REMOVE else None
        if self.Parent.options & OPT_NAME_EDIT:
            self.ctrl_label = self._add_suggest_ctrl()
        else:
            self.ctrl_label = self._add_label_ctrl()
        self.btn_peak = self._add_peak_btn() if self.Parent.options & OPT_BTN_PEAK else None
        self.btn_tint = self._add_tint_btn() if self.Parent.options & OPT_BTN_TINT else None
        self.btn_show = self._add_visibility_btn() if self.Parent.options & OPT_BTN_SHOW else None
        self.btn_update = self._add_update_btn() if self.Parent.options & OPT_BTN_UPDATE else None

        # The spacer is responsible for creating padding on the right side of the header panel
        self._sz.AddSpacer((64, 16))

        # Set the sizer of the Control
        self.SetSizerAndFit(self._sz)

        self.Bind(wx.EVT_SIZE, self.on_size)
        self.Layout()

    # Control creation methods

    def _add_remove_btn(self):
        """ Add a button for stream removal """
        btn_rem = buttons.ImageButton(self.Parent,
                                      bitmap=img.getBitmap("icon/ico_rem_str.png"),
                                      size=self.BUTTON_SIZE)
        btn_rem.bmpHover = img.getBitmap("icon/ico_rem_str_h.png")
        btn_rem.SetToolTipString("Remove stream")
        self._add_ctrl(btn_rem)
        return btn_rem

    def _add_suggest_ctrl(self):
        """ Add a suggest control to the header panel """
        suggest_ctrl = SuggestTextCtrl(self, id=-1, value=self.Parent.stream.name.value)
        suggest_ctrl.SetBackgroundColour(self.Parent.GetBackgroundColour())
        suggest_ctrl.SetForegroundColour(FG_COLOUR_EDIT)
        suggest_ctrl.Bind(wx.EVT_COMMAND_ENTER, self._on_label_change)

        self._add_ctrl(suggest_ctrl, stretch=True)
        return suggest_ctrl

    def _add_label_ctrl(self):
        """ Add a label control to the header panel """
        label_ctrl = wx.StaticText(self, -1, self.Parent.stream.name.value)
        label_ctrl.SetBackgroundColour(self.Parent.GetBackgroundColour())
        label_ctrl.SetForegroundColour(FG_COLOUR_MAIN)
        self._add_ctrl(label_ctrl, stretch=True)
        return label_ctrl

    def _add_tint_btn(self):
        """ Add a tint button to the stream header"""

        tint_btn = buttons.ColourButton(
            self, -1,
            size=self.BUTTON_SIZE,
            colour=self.Parent.stream.tint.value,
            use_hover=True
        )
        tint_btn.SetToolTipString("Stream display colour")

        # Tint event handlers
        tint_btn.Bind(wx.EVT_BUTTON, self._on_tint_click)
        self.Parent.stream.tint.subscribe(self._on_tint_value)

        self._add_ctrl(tint_btn)
        return tint_btn

    def _add_peak_btn(self):
        """ Add the peak toggle button to the stream panel header """
        peak_btn = buttons.ImageStateButton(self, bitmap=img.getBitmap("icon/ico_peak_none.png"))
        peak_btn.bmpHover = img.getBitmap("icon/ico_peak_none_h.png")
        peak_btn.bmpSelected = [img.getBitmap("icon/ico_peak_%s.png" % (m,)) for m in ("gaussian", "lorentzian")]
        peak_btn.bmpSelectedHover = [img.getBitmap("icon/ico_peak_%s_h.png" % (m,)) for m in ("gaussian", "lorentzian")]

        peak_btn.SetToolTipString("Select peak fitting (Gaussian, Lorentzian, or none)")
        self._add_ctrl(peak_btn)
        return peak_btn

    def _add_visibility_btn(self):
        """ Add the visibility toggle button to the stream panel header """
        visibility_btn = buttons.ImageToggleButton(self,
                                                              bitmap=img.getBitmap("icon/ico_eye_closed.png"))
        visibility_btn.bmpHover = img.getBitmap("icon/ico_eye_closed_h.png")
        visibility_btn.bmpSelected = img.getBitmap("icon/ico_eye_open.png")
        visibility_btn.bmpSelectedHover = img.getBitmap("icon/ico_eye_open_h.png")

        visibility_btn.SetToolTipString("Toggle stream visibility")
        self._add_ctrl(visibility_btn)
        return visibility_btn

    def _add_update_btn(self):
        """ Add a button for (de)activation of the stream """
        update_btn = buttons.ImageToggleButton(self,
                                                          bitmap=img.getBitmap("icon/ico_pause.png"))
        update_btn.bmpHover = img.getBitmap("icon/ico_pause_h.png")
        update_btn.bmpSelected = img.getBitmap("icon/ico_play.png")
        update_btn.bmpSelectedHover = img.getBitmap("icon/ico_play_h.png")

        # TODO: add a tooltip for when selected ("Turn off stream" vs "Activate stream")
        # => on ImageToggleButton
        update_btn.SetToolTipString("Update stream")

        self._vac_updated = VigilantAttributeConnector(
            self.Parent.stream.should_update,
            update_btn,
            update_btn.SetToggle,
            update_btn.GetToggle,
            events=wx.EVT_BUTTON
        )
        self._add_ctrl(update_btn)
        return update_btn

    def _add_ctrl(self, ctrl, stretch=False):
        """ Add the given control to the header panel

        :param ctrl: (wx.Control) Control to add to the header panel
        :param stretch: True if the control should expand to fill space

        """

        # Only the first element has a left border
        border = wx.ALL if self._sz.IsEmpty() else wx.RIGHT

        self._sz.Add(
            ctrl,
            proportion=1 if stretch else 0,
            flag=(border | wx.ALIGN_CENTRE_VERTICAL | wx.RESERVE_SPACE_EVEN_IF_HIDDEN),
            border=self.BUTTON_BORDER_SIZE
        )

    # END Control creation methods

    # Layout and painting

    def on_size(self, event):
        """ Handle the wx.EVT_SIZE event for the Expander class """
        self.SetSize((self.Parent.GetSize().x, -1))
        self.Layout()
        self.Refresh()
        event.Skip()

    def on_draw_expander(self, dc):
        """ Draw the expand/collapse arrow icon

        It needs to be called from the parent's paint event handler.
        """
        win_rect = self.GetRect()
        x_pos = win_rect.GetRight() - ICON_WIDTH - CAPTION_PADDING_RIGHT

        self._foldIcons.Draw(
            1 if self.Parent.collapsed else 0,
            dc,
            x_pos,
            (win_rect.GetHeight() - ICON_HEIGHT) // 2,
            wx.IMAGELIST_DRAW_TRANSPARENT
        )

    # END Layout and painting

    # Show/hide/disable controls

    def _show_ctrl(self, ctrl, show):
        """ Show or hide the given control """
        if ctrl:
            self._sz.Show(ctrl, show)
            self._sz.Layout()

    def show_remove_btn(self, show):
        """ Show or hide the remove button """
        self._show_ctrl(self.btn_remove, show)

    def show_updated_btn(self, show):
        """ Show or hide the update button """
        self._show_ctrl(self.btn_update, show)

    def show_peak_btn(self, show):
        """ Show or hide the peak button """
        self._show_ctrl(self.btn_peak, show)

    def show_show_btn(self, show):
        """ Show or hide the show button """
        self._show_ctrl(self.btn_show, show)

    def show_tint_btn(self, show):
        """ Show or hide the tint button """
        self._show_ctrl(self.btn_tint, show)

    def enable_remove_btn(self, enabled):
        """ Enable or disable the remove button """
        self.btn_remove.Enable(enabled)

    def enable_updated_btn(self, enabled):
        """ Enable or disable the update button """
        self.btn_update.Enable(enabled)

    def enable_show_btn(self, enabled):
        """ Enable or disable the show button """
        self.btn_show.Enable(enabled)

    def enable_peak_btn(self, enabled):
        """ Enable or disable the peak button """
        self.btn_peak.Enable(enabled)

    def enable_tint_btn(self, enabled):
        """ Enable or disable the tint button """
        self.btn_tint.Enable(enabled)

    def enable(self, enabled):
        """ Enable or disable all buttons that are present """

        if self.btn_remove:
            self.enable_remove_btn(enabled)

        if self.btn_update:
            self.enable_updated_btn(enabled)

        if self.btn_show:
            self.enable_show_btn(enabled)

        if self.btn_peak:
            self.enable_peak_btn(enabled)

        if self.btn_tint:
            self.enable_tint_btn(enabled)

    def to_static_mode(self):
        """ Remove or disable the controls not needed for a static view of the stream """
        self.show_remove_btn(False)
        self.show_updated_btn(False)
        if isinstance(self.ctrl_label, SuggestTextCtrl):
            self.ctrl_label.Disable()

    def to_locked_mode(self):
        """ Remove or disable all controls """
        self.to_static_mode()
        self.show_show_btn(False)
        self.show_peak_btn(False)

    # END Show/hide/disable controls

    # GUI event handlers

    def _on_label_change(self, evt):
        """ Call the label change callback when the label value changes """
        if callable(self.label_change_callback):
            self.label_change_callback(self.ctrl_label.GetValue())

    @call_in_wx_main
    def _on_tint_value(self, colour):
        """ Update the colour button to reflect the provided colour """
        self.btn_tint.set_colour(colour)

    def _on_tint_click(self, evt):
        """ Handle the mouse click event on the tint button """
        # Remove the hover effect
        self.btn_tint.OnLeave(evt)

        # Set default colour to the current value
        cldata = wx.ColourData()
        cldata.SetColour(wx.Colour(*self.Parent.stream.tint.value))

        dlg = wx.ColourDialog(self, cldata)

        if dlg.ShowModal() == wx.ID_OK:
            colour = dlg.ColourData.GetColour().Get()  # convert to a 3-tuple
            logging.debug("Colour %r selected", colour)
            # Setting the VA will automatically update the button's colour
            self.Parent.stream.tint.value = colour

    # END GUI event handlers

    def set_label_choices(self, choices):
        """ Assign a list of predefined labels to the suggest control form which the user may choose

        :param choices: [str]

        """
        try:
            self.ctrl_label.SetChoices(choices)
        except AttributeError:
            raise TypeError("SuggestTextCtrl required, %s found!!" % type(self.ctrl_label))

    def set_focus_on_label(self):
        """ Set the focus on the label (and select the text if it's editable) """
        self.ctrl_label.SetFocus()
        if self.Parent.options & OPT_NAME_EDIT:
            self.ctrl_label.SelectAll()


class StreamPanel(wx.Panel):
    """ The StreamPanel class, a special case collapsible panel.

    The StreamPanel consists of the following widgets:

        StreamPanel
            BoxSizer
                StreamPanelHeader
                Panel
                    BoxSizer
                        GridBagSizer

    Additional controls can be added to the GridBagSizer in the 'finalize' method.

    The controls contained within a StreamPanel are typically connected to the VigilantAttribute
    properties of the Stream it's representing.

    """

    def __init__(self, parent, stream, options=(OPT_BTN_REMOVE | OPT_BTN_SHOW | OPT_BTN_UPDATE),
                 wid=wx.ID_ANY, pos=wx.DefaultPosition, size=wx.DefaultSize,
                 style=wx.CP_DEFAULT_STYLE, name="StreamPanel", collapsed=False):
        """
        :param parent: (StreamBar) The parent widget.
        :param stream: (Stream) The stream data model to be displayed to and
            modified by the user.
        """
        assert(isinstance(parent, StreamBar))
        wx.Panel.__init__(self, parent, wid, pos, size, style, name)

        self.options = options
        self.stream = stream  # TODO: Should this also be moved to the StreamController? YES!
        # Dye attributes
        self._btn_excitation = None
        self._btn_emission = None

        # Appearance
        # self._agwStyle = agwStyle | wx.CP_NO_TLW_RESIZE  # |wx.CP_GTK_EXPANDER
        self.SetBackgroundColour(BG_COLOUR_STREAM)
        self.SetForegroundColour(FG_COLOUR_MAIN)

        # State

        self._collapsed = collapsed

        # Child widgets

        self.main_sizer = wx.BoxSizer(wx.VERTICAL)
        self.SetSizer(self.main_sizer)

        self._header = None
        self._panel = None

        self._prev_drange = None

        self.gb_sizer = wx.GridBagSizer()

        # Counter that keeps track of the number of rows containing controls inside this panel
        self.num_rows = 0

        self._create_controls()

    def _create_controls(self):
        """ Set up the basic structure for the controls that are going to be used """

        # Create stream header

        self._header = StreamPanelHeader(self)
        self._header.Bind(wx.EVT_LEFT_UP, self.on_toggle)
        self._header.Bind(wx.EVT_PAINT, self.on_draw_expander)

        self.Bind(wx.EVT_BUTTON, self.on_button, self._header)

        self._header.btn_remove.Bind(wx.EVT_BUTTON, self.on_remove_btn)
        self._header.btn_show.Bind(wx.EVT_BUTTON, self.on_visibility_btn)
        if self._header.btn_peak is not None:
            self._header.btn_peak.Bind(wx.EVT_BUTTON, self.on_peak_btn)

        if wx.Platform == "__WXMSW__":
            self._header.Bind(wx.EVT_LEFT_DCLICK, self.on_button)

        self.main_sizer.Add(self._header, 0, wx.EXPAND)

        # Create the control panel

        self._panel = wx.Panel(self, style=wx.TAB_TRAVERSAL | wx.NO_BORDER)

        # Add a simple sizer so we can create padding for the panel
        border_sizer = wx.BoxSizer(wx.HORIZONTAL)
        border_sizer.Add(self.gb_sizer, border=5, flag=wx.ALL | wx.EXPAND, proportion=1)

        self._panel.SetSizer(border_sizer)

        self._panel.SetBackgroundColour(BG_COLOUR_MAIN)
        self._panel.SetForegroundColour(FG_COLOUR_MAIN)
        self._panel.SetFont(self.GetFont())

        self.collapse()

        self.main_sizer.Add(self._panel, 0, wx.EXPAND)

    @property
    def collapsed(self):
        return self._collapsed

    @property
    def header_change_callback(self):
        return self._header.label_change_callback

    @header_change_callback.setter
    def header_change_callback(self, f):
        self._header.label_change_callback = f

    def set_header_choices(self, choices):
        self._header.set_label_choices(choices)

    def flatten(self):
        """ Unfold the stream panel and hide the header """
        self.collapse(False)
        self._header.Show(False)

    def set_focus_on_label(self):
        """ Focus the text label in the header """
        self._header.set_focus_on_label()

    def Layout(self, *args, **kwargs):
        """ Layout the StreamPanel. """

        if not self._header or not self._panel or not self.main_sizer:
            return False  # we need to complete the creation first!

        oursz = self.GetSize()

        # move & resize the button and the static line
        self.main_sizer.SetDimension(0, 0, oursz.GetWidth(),
                                     self.main_sizer.GetMinSize().GetHeight())
        self.main_sizer.Layout()

        if not self._collapsed:
            # move & resize the container window
            yoffset = self.main_sizer.GetSize().GetHeight()
            if oursz.y - yoffset > 0:
                self._panel.SetDimensions(0, yoffset, oursz.x, oursz.y - yoffset)
                # this is very important to make the pane window layout show
                # correctly
                self._panel.Show()
                self._panel.Layout()

        return True

    def DoGetBestSize(self, *args, **kwargs):
        """ Gets the size which best suits the window

        For a control, it would be the minimal size which doesn't truncate the control, for a panel
        the same size as it would have after a call to `Fit()`.

        TODO: This method seems deprecated. Test if it's really so.

        """

        # do not use GetSize() but rather GetMinSize() since it calculates
        # the required space of the sizer
        sz = self.main_sizer.GetMinSize()

        # when expanded, we need more space
        if not self._collapsed:
            pbs = self._panel.GetBestSize()
            sz.width = max(sz.GetWidth(), pbs.x)
            sz.height = sz.y + pbs.y

        return sz

    def Destroy(self, *args, **kwargs):
        """ Delete the widget from the GUI

        """

        # Avoid receiving data after the object is deleted
        if hasattr(self, "_sld_hist"):
            self.stream.histogram.unsubscribe(self.on_histogram)
        if hasattr(self, "_sld_spec"):
            self.stream.image.unsubscribe(self.on_new_spec_data)

        super(StreamPanel, self).Destroy(*args, **kwargs)

    def set_visible(self, visible):
        """ Set the "visible" toggle button of the stream panel """
        self._header.btn_show.SetToggle(visible)

    def set_peak(self, state):
        """ Set the "peak" toggle button of the stream panel
        state (None or 0<=int): None for no peak, 0 for gaussian, 1 for lorentzian
        """
        self._header.btn_peak.SetState(state)

    def collapse(self, collapse=None):
        """ Collapses or expands the pane window """

        if collapse is not None and self._collapsed == collapse:
            return

        self.Freeze()

        # update our state
        self._panel.Show(not collapse)
        self._collapsed = collapse

        # Call after is used, so the fit will occur after everything has been hidden or shown
        wx.CallAfter(self.Parent.fit_streams)

        self.Thaw()

    # GUI events: update the stream when the user changes the values

    def on_remove_btn(self, evt):
        logging.debug("Remove button clicked for '%s'", self.stream.name.value)

        # generate EVT_STREAM_REMOVE
        event = stream_remove_event(spanel=self)
        wx.PostEvent(self, event)

    def on_visibility_btn(self, evt):
        # generate EVT_STREAM_VISIBLE
        event = stream_visible_event(visible=self._header.btn_show.GetToggle())
        wx.PostEvent(self, event)

    def on_peak_btn(self, evt):
        # generate EVT_STREAM_PEAK
        event = stream_peak_event(state=self._header.btn_peak.GetState())
        wx.PostEvent(self, event)

    # Manipulate expander buttons

    def show_updated_btn(self, show):
        self._header.show_updated_btn(show)

    def enable_updated_btn(self, enabled):
        self._header.enable_updated_btn(enabled)

    def show_remove_btn(self, show):
        self._header.show_remove_btn(show)

    def show_visible_btn(self, show):
        self._header.show_show_btn(show)

    def show_peak_btn(self, show):
        self._header.show_peak_btn(show)

    def enable(self, enabled):
        self._header.enable(enabled)

    def OnSize(self, event):
        """ Handles the wx.EVT_SIZE event for StreamPanel
        """
        self.Layout()
        event.Skip()

    def on_toggle(self, evt):
        """ Detect click on the collapse button of the StreamPanel """

        w = evt.GetEventObject().GetSize().GetWidth()

        if evt.GetX() > w * 0.85:
            self.collapse(not self._collapsed)
        else:
            evt.Skip()

    def on_button(self, event):
        """ Handles the wx.EVT_BUTTON event for StreamPanel """

        if event.GetEventObject() != self._header:
            event.Skip()
            return

        self.collapse(not self._collapsed)

    def on_draw_expander(self, event):
        """ Handle the ``wx.EVT_PAINT`` event for the stream panel
        :note: This is a drawing routine to paint the GTK-style expander.
        """

        dc = wx.AutoBufferedPaintDC(self._header)
        dc.SetBackground(wx.Brush(self.GetBackgroundColour()))
        dc.Clear()

        self._header.on_draw_expander(dc)

    def to_static_mode(self):
        """ Hide or make read-only any button or data that should not change during acquisition """
        self._header.to_static_mode()

    def to_locked_mode(self):
        """ Hide or make read-only all buttons and data controls"""
        self._header.to_static_mode()
        self._header.to_locked_mode()

    # Setting Control Addition Methods

    def _add_side_label(self, label_text, tooltip=None):
        """ Add a text label to the control grid

        This method should only be called from other methods that add control to the control grid

        :param label_text: (str)
        :return: (wx.StaticText)

        """

        lbl_ctrl = wx.StaticText(self._panel, -1, label_text)
        if tooltip:
            lbl_ctrl.SetToolTipString(tooltip)

        self.gb_sizer.Add(lbl_ctrl, (self.num_rows, 0),
                          flag=wx.ALL | wx.ALIGN_CENTER_VERTICAL, border=5)
        return lbl_ctrl

    @control_bookkeeper
    def add_autobc_ctrls(self):
        """ Create and return controls needed for (auto) brightness and contrast manipulation """

        btn_autobc = buttons.ImageTextToggleButton(self._panel, height=24,
                                                   icon=img.getBitmap("icon/ico_contrast.png"),
                                                   label="Auto")
        btn_autobc.SetToolTipString("Toggle image auto brightness/contrast")

        lbl_bc_outliers = wx.StaticText(self._panel, -1, "Outliers")
        sld_bc_outliers = UnitFloatSlider(
            self._panel,
            value=self.stream.auto_bc_outliers.value,
            min_val=self.stream.auto_bc_outliers.range[0],
            max_val=self.stream.auto_bc_outliers.range[1],
            unit="%",
            scale="cubic",
            accuracy=2
        )

        sld_bc_outliers.SetToolTipString("Amount of dark and bright pixels to ignore")

        autobc_sz = wx.BoxSizer(wx.HORIZONTAL)
        autobc_sz.Add(btn_autobc, 0, flag=wx.ALIGN_CENTRE_VERTICAL | wx.RIGHT, border=5)
        autobc_sz.Add(lbl_bc_outliers, 0, flag=wx.ALIGN_CENTRE_VERTICAL | wx.LEFT, border=5)
        autobc_sz.Add(sld_bc_outliers, 1,
                      flag=wx.ALIGN_CENTRE_VERTICAL | wx.LEFT | wx.EXPAND, border=5)
        self.gb_sizer.Add(autobc_sz, (self.num_rows, 0), span=(1, 3),
                          flag=wx.ALIGN_CENTRE_VERTICAL | wx.EXPAND | wx.ALL, border=5)

        return btn_autobc, lbl_bc_outliers, sld_bc_outliers

    @control_bookkeeper
    def add_outliers_ctrls(self):
        """ Add controls for the manipulation of the outlier values """

        # TODO: Move min/max to controller too?
        hist_min = self.stream.intensityRange.range[0][0]
        hist_max = self.stream.intensityRange.range[1][1]

        sld_hist = VisualRangeSlider(self._panel, size=(-1, 40),
                                     value=self.stream.intensityRange.value,
                                     min_val=hist_min, max_val=hist_max)
        sld_hist.SetBackgroundColour("#000000")

        self.gb_sizer.Add(sld_hist, pos=(self.num_rows, 0), span=(1, 3), border=5,
                          flag=wx.EXPAND | wx.TOP | wx.LEFT | wx.RIGHT)
        self.num_rows += 1

        # Low/ High values are in raw data. So it's typically uint, but could
        # be float for some weird cases. So we make them float, with high
        # accuracy to avoid rounding.

        lbl_lowi = wx.StaticText(self._panel, -1, "Low")
        tooltip_txt = "Value mapped to black"
        lbl_lowi.SetToolTipString(tooltip_txt)

        txt_lowi = FloatTextCtrl(self._panel, -1,
                                 self.stream.intensityRange.value[0],
                                 style=wx.NO_BORDER, size=(-1, 14),
                                 min_val=hist_min, max_val=hist_max,
                                 key_step=1, accuracy=6)
        txt_lowi.SetForegroundColour(FG_COLOUR_EDIT)
        txt_lowi.SetOwnBackgroundColour(BG_COLOUR_MAIN)

        txt_lowi.SetToolTipString(tooltip_txt)

        lbl_highi = wx.StaticText(self._panel, -1, "High")

        tooltip_txt = "Value mapped to white"
        lbl_highi.SetToolTipString(tooltip_txt)
        txt_highi = FloatTextCtrl(self._panel, -1,
                                  self.stream.intensityRange.value[1],
                                  style=wx.NO_BORDER, size=(-1, 14),
                                  min_val=hist_min, max_val=hist_max,
                                  key_step=1, accuracy=6)
        txt_highi.SetBackgroundColour(BG_COLOUR_MAIN)
        txt_highi.SetForegroundColour(FG_COLOUR_EDIT)
        txt_highi.SetToolTipString(tooltip_txt)

        # Add controls to sizer for spacing
        lh_sz = wx.BoxSizer(wx.HORIZONTAL)

        lh_sz.Add(lbl_lowi, 0, border=5, flag=wx.ALIGN_CENTRE_VERTICAL | wx.LEFT)
        lh_sz.Add(txt_lowi, 1, border=5,
                  flag=wx.ALIGN_CENTRE_VERTICAL | wx.EXPAND | wx.RIGHT | wx.LEFT)
        lh_sz.Add(lbl_highi, 0, border=5, flag=wx.ALIGN_CENTRE_VERTICAL | wx.LEFT)
        lh_sz.Add(txt_highi, 1, border=5,
                  flag=wx.ALIGN_CENTRE_VERTICAL | wx.EXPAND | wx.RIGHT | wx.LEFT)

        # Add spacing sizer to grid sizer
        self.gb_sizer.Add(lh_sz, (self.num_rows, 0), span=(1, 3), border=5,
                          flag=wx.BOTTOM | wx.ALIGN_CENTRE_VERTICAL | wx.EXPAND)

        return sld_hist, txt_lowi, txt_highi

    @control_bookkeeper
    def add_hw_setting_ctrl(self, name, value=None):
        """ Add a generic number control to manipulate a hardware setting """

        lbl_ctrl = self._add_side_label(name)
        value_ctrl = FloatTextCtrl(self._panel, -1, value or 0.0, style=wx.NO_BORDER)

        value_ctrl.SetForegroundColour(gui.FG_COLOUR_EDIT)
        value_ctrl.SetBackgroundColour(gui.BG_COLOUR_MAIN)

        self.gb_sizer.Add(value_ctrl, (self.num_rows, 1), span=(1, 3),
                          flag=wx.ALIGN_CENTRE_VERTICAL | wx.EXPAND | wx.ALL, border=5)

        return lbl_ctrl, value_ctrl

    def _add_slider(self, klass, label_text, value, conf):
        """ Add a slider of type 'klass' to the settings panel """

        lbl_ctrl = self._add_side_label(label_text)
        value_ctrl = klass(self._panel, value=value, **conf)
        self.gb_sizer.Add(value_ctrl, (self.num_rows, 1), span=(1, 3),
                          flag=wx.ALIGN_CENTRE_VERTICAL | wx.EXPAND | wx.ALL, border=5)

        return lbl_ctrl, value_ctrl

    @control_bookkeeper
    def add_slider(self, label_text, value=None, conf=None):
        """ Add an integer value slider to the settings panel

        :param label_text: (str) Label text to display
        :param value: (None or int) Value to display
        :param conf: (None or dict) Dictionary containing parameters for the control

        """
        return self._add_slider(Slider, label_text, value, conf)

    @control_bookkeeper
    def add_integer_slider(self, label_text, value=None, conf=None):
        """ Add an integer value slider to the settings panel

        :param label_text: (str) Label text to display
        :param value: (None or int) Value to display
        :param conf: (None or dict) Dictionary containing parameters for the control

        """
        return self._add_slider(UnitIntegerSlider, label_text, value, conf)

    @control_bookkeeper
    def add_float_slider(self, label_text, value=None, conf=None):
        """ Add a float value slider to the settings panel

        :param label_text: (str) Label text to display
        :param value: (None or float) Value to display
        :param conf: (None or dict) Dictionary containing parameters for the control

        """
        return self._add_slider(UnitFloatSlider, label_text, value, conf)

    @control_bookkeeper
    def add_int_field(self, label_text, value=None, conf=None):
        """ Add an integer value field to the settings panel

        :param label_text: (str) Label text to display
        :param value: (None or int) Value to display
        :param conf: (None or dict) Dictionary containing parameters for the control

        """

        return self._add_num_field(UnitIntegerCtrl, label_text, value, conf)

    @control_bookkeeper
    def add_float_field(self, label_text, value=None, conf=None):
        """ Add a float value field to the settings panel

        :param label_text: (str) Label text to display
        :param value: (None or float) Value to display
        :param conf: (None or dict) Dictionary containing parameters for the control

        """

        return self._add_num_field(UnitFloatCtrl, label_text, value, conf)

    def _add_num_field(self, klass, label_text, value, conf):

        lbl_ctrl = self._add_side_label(label_text)
        value_ctrl = klass(self._panel, value=value, style=wx.NO_BORDER, **conf)
        self.gb_sizer.Add(value_ctrl, (self.num_rows, 1),
                          flag=wx.ALL | wx.EXPAND | wx.ALIGN_CENTER_VERTICAL, border=5)
        value_ctrl.SetForegroundColour(gui.FG_COLOUR_EDIT)
        value_ctrl.SetBackgroundColour(gui.BG_COLOUR_MAIN)

        return lbl_ctrl, value_ctrl

    @control_bookkeeper
    def add_combobox_control(self, label_text, value=None, conf=None):
        """ Add a combobox control to manipulate a hardware setting """

        lbl_ctrl = self._add_side_label(label_text)

        value_ctrl = ComboBox(self._panel, wx.ID_ANY, pos=(0, 0), size=(-1, 16),
                              style=wx.NO_BORDER | wx.TE_PROCESS_ENTER, **conf if conf else {})

        self.gb_sizer.Add(value_ctrl, (self.num_rows, 1), span=(1, 3),
                          flag=wx.ALIGN_CENTRE_VERTICAL | wx.EXPAND | wx.ALL, border=5)

        if value is not None:
            value_ctrl.SetValue(unicode(value))

        return lbl_ctrl, value_ctrl

    @control_bookkeeper
    def add_readonly_field(self, label_text, value=None, selectable=True):
        """ Adds a value to the control panel that cannot directly be changed by the user

        :param label_text: (str) Label text to display
        :param value: (None or object) Value to display next to the label.
           If None, only the label will be displayed. The object should be
           "stringable", so the safest is to ensure it's a string.
        :param selectable: (boolean) whether the value can be selected for copying by the user

        :return: (Ctrl, Ctrl or None) Label and value control

        """

        lbl_ctrl = self._add_side_label(label_text)

        if value is not None:
            if selectable:
                value_ctrl = wx.TextCtrl(self._panel, value=unicode(value),
                                         style=wx.BORDER_NONE | wx.TE_READONLY)
                value_ctrl.SetForegroundColour(gui.FG_COLOUR_DIS)
                value_ctrl.SetBackgroundColour(gui.BG_COLOUR_MAIN)
                self.gb_sizer.Add(value_ctrl, (self.num_rows, 1),
                                  flag=wx.ALL | wx.EXPAND | wx.ALIGN_CENTER_VERTICAL, border=5)
            else:
                value_ctrl = wx.StaticText(self._panel, label=unicode(value))
                value_ctrl.SetForegroundColour(gui.FG_COLOUR_DIS)
                self.gb_sizer.Add(value_ctrl, (self.num_rows, 1), flag=wx.ALL, border=5)
        else:
            value_ctrl = None

        return lbl_ctrl, value_ctrl

    @control_bookkeeper
    def add_checkbox_control(self, label_text, value=True, conf=None):
        """ Add a checkbox to the settings panel

        :param label_text: (str) Label text to display
        :param value: (bool) Value to display (True == checked)
        :param conf: (None or dict) Dictionary containing parameters for the control

        """
        if conf is None:
            conf = {}

        lbl_ctrl = self._add_side_label(label_text)
        # wx.ALIGN_RIGHT has the effect of only highlighting the box on hover,
        # which makes it less ugly with Ubuntu
        value_ctrl = wx.CheckBox(self._panel, wx.ID_ANY,
                                 style=wx.ALIGN_RIGHT | wx.NO_BORDER,
                                 **conf)
        self.gb_sizer.Add(value_ctrl, (self.num_rows, 1), span=(1, 3),
                          flag=wx.ALIGN_CENTRE_VERTICAL | wx.EXPAND | wx.TOP | wx.BOTTOM, border=5)
        value_ctrl.SetValue(value)

        return lbl_ctrl, value_ctrl

    @control_bookkeeper
    def add_radio_control(self, label_text, value=None, conf=None):
        """ Add a series of radio buttons to the settings panel

        :param label_text: (str) Label text to display
        :param value: (None or float) Value to display
        :param conf: (None or dict) Dictionary containing parameters for the control

        """

        lbl_ctrl = self._add_side_label(label_text)
        value_ctrl = GraphicalRadioButtonControl(self._panel, -1, style=wx.NO_BORDER,
                                                 **conf if conf else {})
        self.gb_sizer.Add(value_ctrl, (self.num_rows, 1),
                          flag=wx.ALL | wx.EXPAND | wx.ALIGN_CENTER_VERTICAL, border=5)

        if value is not None:
            value_ctrl.SetValue(value)

        return lbl_ctrl, value_ctrl

    @control_bookkeeper
    def add_text_field(self, label_text, value=None, readonly=False):
        """ Add a label and text control to the settings panel

        :param label_text: (str) Label text to display
        :param value: (None or str) Value to display
        :param readonly: (boolean) Whether the value can be changed by the user

        :return: (Ctrl, Ctrl) Label and text control

        """

        lbl_ctrl = self._add_side_label(label_text)
        value_ctrl = wx.TextCtrl(self._panel, value=unicode(value or ""),
                                 style=wx.TE_PROCESS_ENTER | wx.BORDER_NONE | (wx.TE_READONLY if readonly else 0))
        if readonly:
            value_ctrl.SetForegroundColour(gui.FG_COLOUR_DIS)
        else:
            value_ctrl.SetForegroundColour(gui.FG_COLOUR_EDIT)
        value_ctrl.SetBackgroundColour(gui.BG_COLOUR_MAIN)
        self.gb_sizer.Add(value_ctrl, (self.num_rows, 1),
                          flag=wx.ALL | wx.EXPAND | wx.ALIGN_CENTER_VERTICAL, border=5)

        return lbl_ctrl, value_ctrl

    @control_bookkeeper
    def add_divider(self):
        """ Add a dividing line to the stream panel """
        line_ctrl = wx.StaticLine(self._panel, size=(-1, 1))
        self.gb_sizer.Add(line_ctrl, (self.num_rows, 0), span=(1, 3),
                          flag=wx.ALL | wx.EXPAND, border=5)

    @control_bookkeeper
    def add_dye_excitation_ctrl(self, band, readonly, center_wl_color):
        lbl_ctrl, value_ctrl, lbl_exc_peak, btn_excitation = self._add_filter_line("Excitation",
                                                                                   band,
                                                                                   readonly,
                                                                                   center_wl_color)
        return lbl_ctrl, value_ctrl, lbl_exc_peak, btn_excitation

    @control_bookkeeper
    def add_dye_emission_ctrl(self, band, readonly, center_wl_color):
        lbl_ctrl, value_ctrl, lbl_em_peak, btn_emission = self._add_filter_line("Emission",
                                                                                band,
                                                                                readonly,
                                                                                center_wl_color)
        return lbl_ctrl, value_ctrl, lbl_em_peak, btn_emission

    def _add_filter_line(self, name, band, readonly, center_wl_color):
        """ Create the controls for dye emission/excitation colour filter setting

        :param name: (str): the label name
        :param band (str): the current wavelength band to display
        :param readonly (bool) read-only when there's no or just one band value
        :param center_wl_color: None or (r, g, b) center wavelength color of the
           current band of the VA. If None, no button is shown.

        :return: (4 wx.Controls) the respective controls created

        """

        # Note: va.value is in m, but we present everything in nm
        lbl_ctrl = self._add_side_label(name)

        # will contain both the combo box and the peak label
        exc_sizer = wx.BoxSizer(wx.HORIZONTAL)
        self.gb_sizer.Add(exc_sizer, (self.num_rows, 1), flag=wx.EXPAND)

        if readonly:
            hw_set = wx.TextCtrl(self._panel, value=band, size=(-1, 16),
                                 style=wx.BORDER_NONE | wx.TE_READONLY)
            hw_set.SetBackgroundColour(self._panel.BackgroundColour)
            hw_set.SetForegroundColour(FG_COLOUR_DIS)
            exc_sizer.Add(hw_set, 1, flag=wx.LEFT | wx.RIGHT | wx.ALIGN_CENTRE_VERTICAL, border=5)
        else:
            hw_set = ComboBox(self._panel, value=band, size=(-1, 16),
                              style=wx.CB_READONLY | wx.BORDER_NONE)

            # To avoid catching mouse wheels events when scrolling the panel
            hw_set.Bind(wx.EVT_MOUSEWHEEL, lambda e: None)

            exc_sizer.Add(hw_set, 1, border=5, flag=wx.ALL | wx.ALIGN_CENTRE_VERTICAL)

        # Label for peak information
        lbl_peak = wx.StaticText(self._panel)
        exc_sizer.Add(lbl_peak, 1, border=5, flag=wx.ALL | wx.ALIGN_CENTRE_VERTICAL | wx.ALIGN_LEFT)

        if center_wl_color:
            # A button, but not clickable, just to show the wavelength
            # If a dye is selected, the colour of the peak is used, otherwise we
            # use the hardware setting
            btn_color = buttons.ColourButton(self._panel, -1, colour=center_wl_color,
                                              size=(18, 18))
            self.gb_sizer.Add(btn_color,
                              (self.num_rows, 2),
                              flag=wx.RIGHT | wx.ALIGN_CENTRE_VERTICAL | wx.ALIGN_RIGHT,
                              border=5)
        else:
            btn_color = None

        return lbl_ctrl, hw_set, lbl_peak, btn_color

    # END Setting Control Addition Methods

    @control_bookkeeper
    def add_rgbfit_ctrl(self):
        """ Add an 'rgb fit' button to the stream panel

        :return: (ImageTextToggleButton)

        """

        btn_fit_rgb = buttons.ImageTextToggleButton(self._panel, height=24,
                                                    icon=img.getBitmap("icon/ico_bgr.png"),
                                                    label="RGB")
        btn_fit_rgb.SetToolTipString("Toggle sub-bandwidths to Blue/Green/Red display")

        self.gb_sizer.Add(btn_fit_rgb, (self.num_rows, 0), flag=wx.LEFT | wx.TOP | wx.BOTTOM,
                          border=5)

        return btn_fit_rgb

    @control_bookkeeper
    def add_specbw_ctrls(self):
        """ Add controls to manipulate the spectrum data bandwidth

        Returns:
            (VisualRangeSlider, wx.StaticText, wx.StaticText)

        """

        # 1st row, center label, slider and value

        wl = self.stream.spectrumBandwidth.value

        # TODO: Move min/max to controller too?
        wl_rng = (self.stream.spectrumBandwidth.range[0][0],
                  self.stream.spectrumBandwidth.range[1][1])

        sld_spec = VisualRangeSlider(self._panel, size=(-1, 40),
                                     value=wl, min_val=wl_rng[0], max_val=wl_rng[1])
        sld_spec.SetBackgroundColour("#000000")

        self.gb_sizer.Add(sld_spec, pos=(self.num_rows, 0), span=(1, 3), border=5,
                          flag=wx.EXPAND | wx.TOP | wx.LEFT | wx.RIGHT)
        self.num_rows += 1

        # 2nd row, text fields for intensity (ratios)

        tooltip_txt = "Center wavelength of the spectrum"

        lbl_scenter = wx.StaticText(self._panel, -1, "Center")
        lbl_scenter.SetToolTipString(tooltip_txt)

        txt_scenter = UnitFloatCtrl(self._panel, -1, (wl[0] + wl[1]) / 2,
                                    style=wx.NO_BORDER, size=(-1, 14),
                                    min_val=wl_rng[0], max_val=wl_rng[1],
                                    unit=self.stream.spectrumBandwidth.unit,  # m or px
                                    accuracy=3)

        txt_scenter.SetBackgroundColour(BG_COLOUR_MAIN)
        txt_scenter.SetForegroundColour(FG_COLOUR_EDIT)
        txt_scenter.SetToolTipString(tooltip_txt)

        tooltip_txt = "Bandwidth of the spectrum"
        lbl_sbw = wx.StaticText(self._panel, -1, "Bandwidth")
        lbl_sbw.SetToolTipString(tooltip_txt)

        txt_sbw = UnitFloatCtrl(self._panel, -1, (wl[1] - wl[0]),
                                style=wx.NO_BORDER, size=(-1, 14),
                                min_val=0, max_val=(wl_rng[1] - wl_rng[0]),
                                unit=self.stream.spectrumBandwidth.unit,
                                accuracy=3)
        txt_sbw.SetBackgroundColour(BG_COLOUR_MAIN)
        txt_sbw.SetForegroundColour(FG_COLOUR_EDIT)
        txt_sbw.SetToolTipString(tooltip_txt)

        cb_wl_sz = wx.BoxSizer(wx.HORIZONTAL)
        cb_wl_sz.Add(lbl_scenter, 0,
                     flag=wx.ALIGN_CENTRE_VERTICAL | wx.LEFT,
                     border=5)
        cb_wl_sz.Add(txt_scenter, 1,
                     flag=wx.ALIGN_CENTRE_VERTICAL | wx.EXPAND | wx.RIGHT | wx.LEFT,
                     border=5)
        cb_wl_sz.Add(lbl_sbw, 0,
                     flag=wx.ALIGN_CENTRE_VERTICAL | wx.LEFT,
                     border=5)
        cb_wl_sz.Add(txt_sbw, 1,
                     flag=wx.ALIGN_CENTRE_VERTICAL | wx.EXPAND | wx.RIGHT | wx.LEFT,
                     border=5)
        self.gb_sizer.Add(cb_wl_sz, (self.num_rows, 0), span=(1, 3), border=5,
                          flag=wx.BOTTOM | wx.ALIGN_CENTRE_VERTICAL | wx.EXPAND)

        return sld_spec, txt_scenter, txt_sbw

    @control_bookkeeper
    def add_specselwidth_ctrl(self):
        """ Add a control to manipulate the spectrum selection width

        :return: wx.StaticText, UnitIntegerSlider

        """

        # Add the selectionWidth VA
        tooltip_txt = "Width of the point or line selected"

        lbl_selection_width = self._add_side_label("Width", tooltip_txt)

        sld_selection_width = UnitIntegerSlider(
            self._panel,
            value=self.stream.selectionWidth.value,
            min_val=self.stream.selectionWidth.range[0],
            max_val=self.stream.selectionWidth.range[1],
            unit="px",
        )
        sld_selection_width.SetToolTipString(tooltip_txt)

        self.gb_sizer.Add(sld_selection_width, (self.num_rows, 1), span=(1, 2), border=5,
                          flag=wx.ALIGN_CENTRE_VERTICAL | wx.EXPAND | wx.ALL)

        return lbl_selection_width, sld_selection_width


class StreamBar(wx.Panel):
    """
    The whole panel containing stream panels and a button to add more streams
    There are multiple levels of visibility of a stream panel:
     * the stream panel is shown in the panel and has the visible icon on:
        The current view is compatible with the stream and has it in its list
        of streams.
     * the stream panel is shown in the panel and has the visible icon off:
        The current view is compatible with the stream, but the stream is not
        in its list of streams
     * the stream panel is not present in the panel (hidden):
        The current view is not compatible with the stream
    """

    DEFAULT_BORDER = 2
    DEFAULT_STYLE = wx.BOTTOM | wx.EXPAND
    # the order in which the streams are displayed
    STREAM_ORDER = (
        acq.stream.SEMStream,
        acq.stream.StaticSEMStream,
        acq.stream.BrightfieldStream,
        acq.stream.StaticStream,
        acq.stream.FluoStream,
        acq.stream.CLStream,
        acq.stream.CameraStream,
        acq.stream.ARSettingsStream,
        acq.stream.SpectrumSettingsStream,
        acq.stream.MonochromatorSettingsStream,
        acq.stream.MomentOfInertiaLiveStream,
        acq.stream.CameraCountStream,
    )

    def __init__(self, *args, **kwargs):

        add_btn = kwargs.pop('add_button', False)

        wx.Panel.__init__(self, *args, **kwargs)

        self.stream_panels = []

        self._sz = wx.BoxSizer(wx.VERTICAL)
        self.SetSizer(self._sz)

        msg = "No streams available."
        self.txt_no_stream = wx.StaticText(self, -1, msg)
        self._sz.Add(self.txt_no_stream, 0, wx.ALL | wx.ALIGN_CENTER, 10)

        self.btn_add_stream = None

        if add_btn:
            self.btn_add_stream = buttons.PopupImageButton(
                self, -1,
                label="ADD STREAM",
                style=wx.ALIGN_CENTER
            )

            self.btn_add_stream.SetForegroundColour("#999999")
            self._sz.Add(self.btn_add_stream, flag=wx.ALL, border=10)

            # self.btn_add_stream.Bind(wx.EVT_BUTTON, self.on_add_stream)

        self.fit_streams()

    def fit_streams(self):
        # When the whole window/app is destroyed, each widget receives a destroy
        # event. In such a case, it's not worthy re-fitting the streams, and
        # especially it can fail because some other objects have already been
        # destroyed.
        if self.IsBeingDeleted():
            logging.debug("Stream panelbar is being deleted, not refitting")
            return

        logging.debug("Refitting stream panels")
        self._set_warning()

        h = self._sz.GetMinSize().GetHeight()
        self.SetSize((-1, h))

        p = self.Parent
        while not isinstance(p, FoldPanelItem):
            p = p.Parent

        p.Refresh()

    # TODO: maybe should be provided after init by the controller (like key of
    # sorted()), to separate the GUI from the model ?
    def _get_stream_order(self, stream):
        """ Gives the "order" of the given stream, as defined in STREAM_ORDER.

        Args:
            stream (Stream): a stream

        Returns:
            (int >= 0): the order

        """

        for i, c in enumerate(self.STREAM_ORDER):
            if isinstance(stream, c):
                return i

        msg = "Stream %s of unknown order type %s"
        logging.warning(msg, stream.name.value, stream.__class__.__name__)
        return len(self.STREAM_ORDER)

    # === VA handlers

    # Moved to stream controller

    # === Event Handlers

    # def on_add_stream(self, evt):
    #     evt.Skip()

    def on_stream_remove(self, evt):
        """
        Called when user request to remove a stream via the stream panel
        """
        st = evt.spanel.stream
        logging.debug("User removed stream (panel) %s", st.name.value)
        # delete stream panel
        self.remove_stream_panel(evt.spanel)

        # Publish removal notification
        pub.sendMessage("stream.remove", stream=st)

    def on_streamp_destroy(self, evt):
        """
        Called when a stream panel is completely removed
        """
        self.fit_streams()

    # === API of the stream panel
    def show_add_button(self):
        if self.btn_add_stream:
            self.btn_add_stream.Show()
            self.fit_streams()

    def hide_add_button(self):
        if self.btn_add_stream:
            self.btn_add_stream.Hide()
            self.fit_streams()

    def is_empty(self):
        return len(self.stream_panels) == 0

    def get_size(self):
        """ Return the number of streams contained within the StreamBar """
        return len(self.stream_panels)

    def add_stream_panel(self, spanel, show=True):
        """
        This method adds a stream panel to the stream bar. The appropriate
        position is automatically determined.
        spanel (StreamPanel): a stream panel
        """
        # Insert the spanel in the order of STREAM_ORDER. If there are already
        # streams with the same type, insert after them.
        ins_pos = 0
        order_s = self._get_stream_order(spanel.stream)
        for e in self.stream_panels:
            order_e = self._get_stream_order(e.stream)
            if order_s < order_e:
                break
            ins_pos += 1

        logging.debug("Inserting %s at position %s", spanel.stream.__class__.__name__, ins_pos)

        self.stream_panels.insert(ins_pos, spanel)

        if self._sz is None:
            self._sz = wx.BoxSizer(wx.VERTICAL)
            self.SetSizer(self._sz)

        self._sz.InsertWindow(ins_pos, spanel,
                              flag=self.DEFAULT_STYLE,
                              border=self.DEFAULT_BORDER)

        # TODO: instead of a stream_remove message, just take a callable to call
        # when the stream needs to be removed
        spanel.Bind(EVT_STREAM_REMOVE, self.on_stream_remove)
        spanel.Bind(wx.EVT_WINDOW_DESTROY, self.on_streamp_destroy, source=spanel)
        spanel.Layout()

        # hide the stream if the current view is not compatible
        spanel.Show(show)
        self.fit_streams()

    def remove_stream_panel(self, spanel):
        """
        Removes a stream panel
        Deletion of the actual stream must be done separately.
        Must be called in the main GUI thread
        """
        # Remove it from the sizer explicitly, because even if the sizer will
        # eventually detect it (via the destroy event), that will be later, and
        # until then the fit_stream will not be correct.
        self._sz.Detach(spanel)
        self.stream_panels.remove(spanel)
        spanel.Destroy()

    def clear(self):
        """
        Remove all stream panels
        Must be called in the main GUI thread
        """
        for p in list(self.stream_panels):
            # Only refit the (empty) bar after all streams are gone
            p.Unbind(wx.EVT_WINDOW_DESTROY, source=p, handler=self.on_streamp_destroy)
            self.remove_stream_panel(p)

        self.fit_streams()

    def _set_warning(self):
        """ Display a warning text when no streams are present, or show it
        otherwise.
        """
        self.txt_no_stream.Show(self.is_empty())
