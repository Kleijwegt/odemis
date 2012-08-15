
# This module is used to construct Delmic's custom FoldPanelBar according to
# the definition in the XRC configuration file.
#
# This module is used both by Odemis' GUI and XRCED.

import wx
import wx.lib.buttons
import wx.xrc as xrc

import odemis.gui.comp.foldpanelbar as fpb
import odemis.gui.comp.stream as strm
import odemis.gui.comp.buttons as btns
import odemis.gui.comp.text as txt
import odemis.gui.dblmscopepanel as mscp

##################################
# Fold Panel Bar related Handlers
##################################

class FixedStreamPanelEntryXmlHandler(xrc.XmlResourceHandler):
    def __init__(self):
        xrc.XmlResourceHandler.__init__(self)
        # Specify the styles recognized by objects of this type
        self.AddStyle("wxTAB_TRAVERSAL", wx.TAB_TRAVERSAL)
        #self.AddStyle("CP_GTK_EXPANDER", CP_GTK_EXPANDER)
        #self.AddStyle("CP_DEFAULT_STYLE", CP_DEFAULT_STYLE)
        #self.AddStyle("CP_NO_TLW_RESIZE", CP_NO_TLW_RESIZE)
        self.AddWindowStyles()

    # This method and the next one are required for XmlResourceHandlers
    def CanHandle(self, node):
        capable = self.IsOfClass(node, "FixedStreamPanelEntry")

        return capable

    def DoCreateResource(self):
        assert self.GetInstance() is None

        parent_window = self.GetParentAsWindow()
        # Now create the object
        panel = strm.FixedStreamPanelEntry(parent_window,
                                      self.GetID(),
                                      self.GetText('label'),
                                      self.GetPosition(),
                                      self.GetSize(),
                                      self.GetStyle("style", wx.TAB_TRAVERSAL),
                                      #self.GetStyle('exstyle'),
                                      name=self.GetName(),
                                      collapsed=self.GetBool('collapsed')
                                      )

        # These two things should be done in either case:
        # Set standard window attributes
        self.SetupWindow(panel)
        panel.finalize()

        parent_window.add_stream(panel)
        # Create any child windows of this node
        # deprecated: all children are hard-coded
        #self.CreateChildren(panel.get_panel())

        return panel

class CustomStreamPanelEntryXmlHandler(xrc.XmlResourceHandler):
    def __init__(self):
        xrc.XmlResourceHandler.__init__(self)
        # Specify the styles recognized by objects of this type
        self.AddStyle("wxTAB_TRAVERSAL", wx.TAB_TRAVERSAL)
        #self.AddStyle("CP_GTK_EXPANDER", CP_GTK_EXPANDER)
        #self.AddStyle("CP_DEFAULT_STYLE", CP_DEFAULT_STYLE)
        #self.AddStyle("CP_NO_TLW_RESIZE", CP_NO_TLW_RESIZE)
        self.AddWindowStyles()

    # This method and the next one are required for XmlResourceHandlers
    def CanHandle(self, node):
        return self.IsOfClass(node, "CustomStreamPanelEntry")

    def DoCreateResource(self):
        assert self.GetInstance() is None

        # Now create the object
        panel = strm.CustomStreamPanelEntry(self.GetParentAsWindow(),
                                      self.GetID(),
                                      self.GetText('label'),
                                      self.GetPosition(),
                                      self.GetSize(),
                                      self.GetStyle("style", wx.TAB_TRAVERSAL),
                                      #self.GetStyle('exstyle'),
                                      name=self.GetName(),
                                      collapsed=self.GetBool('collapsed')
                                      )

        # These two things should be done in either case:
        # Set standard window attributes
        self.SetupWindow(panel)
        panel.finalize()

        # Create any child windows of this node
        # deprecated: all children are hard-coded
        #self.CreateChildren(panel.get_panel())

        return panel

class FoldPanelBarXmlHandler(xrc.XmlResourceHandler):
    def __init__(self):
        xrc.XmlResourceHandler.__init__(self)
        # Standard styles
        self.AddWindowStyles()
        #self._isInside = False

    def CanHandle(self, node):
        # return not self._isInside and self.IsOfClass(node, 'wx.lib.foldpanelbar.FoldPanelBar') or \
        #        self._isInside and self.IsOfClass(node, 'foldpanel')
        return self.IsOfClass(node, 'FoldPanelBar')


    # Process XML parameters and create the object
    def DoCreateResource(self):

        if self.GetClass() == 'FoldPanelBar':
            #print "Creating FoldpanelBar"
            w = fpb.FoldPanelBar(self.GetParentAsWindow(),
                                 self.GetID(),
                                 self.GetPosition(),
                                 self.GetSize(),
                                 self.GetStyle())

            # if self.HasParam('spacing'):
            #     self.spacing = self.GetLong('spacing')

            # if self.HasParam('leftspacing'):
            #     self.left_spacing = self.GetLong('leftspacing')

            # if self.HasParam('rightspacing'):
            #     self.right_spacing = self.GetLong('rightspacing')

            self.SetupWindow(w)

            parent = w.GetParent()
            if parent.__class__ == wx.ScrolledWindow:
                parent.EnableScrolling(False, True)
                parent.SetScrollbars(-1, 10, 1, 1)

            self.CreateChildren(w, False)

            return w


class FoldPanelItemXmlHandler(xrc.XmlResourceHandler):
    def __init__(self):
        xrc.XmlResourceHandler.__init__(self)
        # Standard styles
        self.AddWindowStyles()
        # Custom styles

    def CanHandle(self, node):
        return self.IsOfClass(node, 'FoldPanelItem')


    # Process XML parameters and create the object
    def DoCreateResource(self):

        if self.GetClass() == 'FoldPanelItem':
            #print "Creating FoldpanelItem"
            parent = self.GetParentAsWindow()
            w = fpb.FoldPanelItem(parent,
                                  self.GetID(),
                                  self.GetPosition(),
                                  self.GetSize(),
                                  self.GetStyle(),
                                  self.GetText('label'),
                                  self.GetBool('collapsed'))
            self.SetupWindow(w)

            self.CreateChildren(w, False)

            # Move all the FoldPanelItem children to the main sizer
            w.children_to_sizer()
            parent.add_item(w)
            return w

################################
# ImageButton sub class handlers
################################

class GenBitmapButtonHandler(xrc.XmlResourceHandler):
    def __init__(self):
        xrc.XmlResourceHandler.__init__(self)
        # Standard styles
        self.AddWindowStyles()
        # Custom styles

    def CanHandle(self, node):
        return self.IsOfClass(node, 'wx.lib.buttons.GenBitmapButton')

    # Process XML parameters and create the object
    def DoCreateResource(self):
        assert self.GetInstance() is None

        bmp = wx.NullBitmap
        if self.GetParamNode("bitmap"):
            bmp = self.GetBitmap("bitmap")

        w = wx.lib.buttons.GenBitmapButton(self.GetParentAsWindow(),
                                    self.GetID(),
                                    bmp,
                                    self.GetPosition(),
                                    self.GetSize(),
                                    self.GetStyle())

        if self.GetParamNode("selected"):
            bmp = self.GetBitmap("selected")
            w.SetBitmapSelected(bmp)

        if self.GetParamNode("focus"):
            bmp = self.GetBitmap("focus")
            w.SetBitmapFocus(bmp)

        if self.GetParamNode("disabled"):
            bmp = self.GetBitmap("disabled")
            w.SetBitmapDisabled(bmp)

        self.SetupWindow(w)
        return w

class ImageButtonHandler(xrc.XmlResourceHandler):

    def __init__(self):
        xrc.XmlResourceHandler.__init__(self)
        # Standard styles
        self.AddWindowStyles()
        # Custom styles

    def CanHandle(self, node):
        return self.IsOfClass(node, 'ImageButton')

    # Process XML parameters and create the object
    def DoCreateResource(self):
        assert self.GetInstance() is None

        bmp = wx.NullBitmap
        if self.GetParamNode("bitmap"):
            bmp = self.GetBitmap("bitmap")

        w = btns.ImageButton(self.GetParentAsWindow(),
                            self.GetID(),
                            bmp,
                            pos=self.GetPosition(),
                            size=self.GetSize(),
                            style=self.GetStyle(),
                            label_delta=self.GetLong('delta'))

        if self.GetParamNode("selected"):
            bmp = self.GetBitmap("selected")
            w.SetBitmapSelected(bmp)

        if self.GetParamNode("hover"):
            bmp = self.GetBitmap("hover")
            w.SetBitmapHover(bmp)

        if self.GetParamNode("focus"):
            bmp = self.GetBitmap("focus")
            w.SetBitmapFocus(bmp)


        if self.GetParamNode("disabled"):
            bmp = self.GetBitmap("disabled")
            w.SetBitmapDisabled(bmp)

        self.SetupWindow(w)
        return w

class ImageTextButtonHandler(xrc.XmlResourceHandler):

    def __init__(self):
        xrc.XmlResourceHandler.__init__(self)
        # Standard styles
        self.AddWindowStyles()
        # Custom styles
        self.AddStyle('wxALIGN_LEFT', wx.ALIGN_LEFT)
        self.AddStyle('wxALIGN_RIGHT', wx.ALIGN_RIGHT)
        self.AddStyle('wxALIGN_CENTRE', wx.ALIGN_CENTRE)

    def CanHandle(self, node):
        return self.IsOfClass(node, 'ImageTextButton')

    # Process XML parameters and create the object
    def DoCreateResource(self):
        assert self.GetInstance() is None

        bmp = wx.NullBitmap
        if self.GetParamNode("bitmap"):
            bmp = self.GetBitmap("bitmap")

        w = btns.ImageTextButton(self.GetParentAsWindow(),
                                 self.GetID(),
                                 bmp,
                                 pos=self.GetPosition(),
                                 size=self.GetSize(),
                                 style=self.GetStyle(),
                                 label=self.GetText('label'),
                                 label_delta=self.GetLong('delta'))

        if self.GetParamNode("selected"):
            bmp = self.GetBitmap("selected")
            w.SetBitmapSelected(bmp)

        if self.GetParamNode("hover"):
            bmp = self.GetBitmap("hover")
            w.SetBitmapHover(bmp)

        if self.GetParamNode("focus"):
            bmp = self.GetBitmap("focus")
            w.SetBitmapFocus(bmp)

        if self.GetParamNode("disabled"):
            bmp = self.GetBitmap("disabled")
            w.SetBitmapDisabled(bmp)

        self.SetupWindow(w)
        return w



class ImageTextToggleButtonHandler(xrc.XmlResourceHandler):

    def __init__(self):
        xrc.XmlResourceHandler.__init__(self)
        # Standard styles
        self.AddWindowStyles()
        # Custom styles
        self.AddStyle('wxALIGN_LEFT', wx.ALIGN_LEFT)
        self.AddStyle('wxALIGN_RIGHT', wx.ALIGN_RIGHT)
        self.AddStyle('wxALIGN_CENTRE', wx.ALIGN_CENTRE)

    def CanHandle(self, node):
        return self.IsOfClass(
            node, 'ImageTextToggleButton')

    # Process XML parameters and create the object
    def DoCreateResource(self):
        assert self.GetInstance() is None

        bmp = wx.NullBitmap
        if self.GetParamNode("bitmap"):
            bmp = self.GetBitmap("bitmap")

        w = btns.ImageTextToggleButton(self.GetParentAsWindow(),
                                       self.GetID(),
                                       bmp,
                                       pos=self.GetPosition(),
                                       size=self.GetSize(),
                                       style=self.GetStyle(),
                                       label=self.GetText('label'),
                                       label_delta=self.GetLong('delta'))

        if self.GetParamNode("selected"):
            bmp = self.GetBitmap("selected")
            w.SetBitmapSelected(bmp)

        if self.GetParamNode("hover"):
            bmp = self.GetBitmap("hover")
            w.SetBitmapHover(bmp)

        if self.GetParamNode("focus"):
            bmp = self.GetBitmap("focus")
            w.SetBitmapFocus(bmp)

        if self.GetParamNode("disabled"):
            bmp = self.GetBitmap("disabled")
            w.SetBitmapDisabled(bmp)

        self.SetupWindow(w)
        return w

class TabButtonButtonHandler(xrc.XmlResourceHandler):

    def __init__(self):
        xrc.XmlResourceHandler.__init__(self)
        # Standard styles
        self.AddWindowStyles()
        # Custom styles
        self.AddStyle('wxALIGN_LEFT', wx.ALIGN_LEFT)
        self.AddStyle('wxALIGN_RIGHT', wx.ALIGN_RIGHT)
        self.AddStyle('wxALIGN_CENTRE', wx.ALIGN_CENTRE)

    def CanHandle(self, node):
        return self.IsOfClass(
            node, 'TabButton')

    # Process XML parameters and create the object
    def DoCreateResource(self):
        assert self.GetInstance() is None

        bmp = wx.NullBitmap
        if self.GetParamNode("bitmap"):
            bmp = self.GetBitmap("bitmap")

        w = btns.TabButton(self.GetParentAsWindow(),
                                       self.GetID(),
                                       bmp,
                                       pos=self.GetPosition(),
                                       size=self.GetSize(),
                                       style=self.GetStyle(),
                                       label=self.GetText('label'),
                                       label_delta=self.GetLong('delta'))

        if self.GetParamNode("selected"):
            bmp = self.GetBitmap("selected")
            w.SetBitmapSelected(bmp)

        if self.GetParamNode("hover"):
            bmp = self.GetBitmap("hover")
            w.SetBitmapHover(bmp)

        if self.GetParamNode("focus"):
            bmp = self.GetBitmap("focus")
            w.SetBitmapFocus(bmp)

        if self.GetParamNode("disabled"):
            bmp = self.GetBitmap("disabled")
            w.SetBitmapDisabled(bmp)

        if self.GetBool('default'):
            w.SetToggle(True)

        self.SetupWindow(w)
        return w

class PopupImageButtonHandler(xrc.XmlResourceHandler):

    def __init__(self):
        xrc.XmlResourceHandler.__init__(self)
        # Standard styles
        self.AddWindowStyles()
        # Custom styles

    def CanHandle(self, node):
        return self.IsOfClass(node, 'PopupImageButton')

    # Process XML parameters and create the object
    def DoCreateResource(self):
        assert self.GetInstance() is None

        bmp = wx.NullBitmap
        if self.GetParamNode("bitmap"):
            bmp = self.GetBitmap("bitmap")

        w = btns.PopupImageButton(self.GetParentAsWindow(),
                                  self.GetID(),
                                  bmp,
                                  pos=self.GetPosition(),
                                  size=self.GetSize(),
                                  style=self.GetStyle())

        if self.GetParamNode("selected"):
            bmp = self.GetBitmap("selected")
            w.SetBitmapSelected(bmp)

        if self.GetParamNode("hover"):
            bmp = self.GetBitmap("hover")
            w.SetBitmapHover(bmp)

        if self.GetParamNode("focus"):
            bmp = self.GetBitmap("focus")
            w.SetBitmapFocus(bmp)

        if self.GetParamNode("disabled"):
            bmp = self.GetBitmap("disabled")
            w.SetBitmapDisabled(bmp)

        self.SetupWindow(w)
        return w

class SuggestTextCtrlHandler(xrc.XmlResourceHandler):

    def __init__(self):
        xrc.XmlResourceHandler.__init__(self)
        # Standard styles
        self.AddWindowStyles()
        # Custom styles

    def CanHandle(self, node):
        return self.IsOfClass(node, 'SuggestTextCtrl')

    # Process XML parameters and create the object
    def DoCreateResource(self):
        assert self.GetInstance() is None

        w = txt.SuggestTextCtrl(self.GetParentAsWindow(),
                                id=self.GetID(),
                                value=self.GetText('value'),
                                pos=self.GetPosition(),
                                size=self.GetSize(),
                                style=self.GetStyle(),
                                choices=[str(i) for i in range(2)])
        self.SetupWindow(w)
        return w

class UnitIntegerCtrlHandler(xrc.XmlResourceHandler):

    def __init__(self):
        xrc.XmlResourceHandler.__init__(self)
        # Standard styles
        self.AddWindowStyles()
        # Custom styles

    def CanHandle(self, node):
        return self.IsOfClass(node, 'UnitIntegerCtrl')

    # Process XML parameters and create the object
    def DoCreateResource(self):
        assert self.GetInstance() is None

        w = txt.UnitIntegerCtrl(self.GetParentAsWindow(),
                                id=self.GetID(),
                                value=self.GetText('value'),
                                pos=self.GetPosition(),
                                size=self.GetSize(),
                                style=self.GetStyle(),
                                unit=self.GetText('unit'),
                                min_val=self.GetLong('min'),
                                max_val=self.GetLong('max'))
        self.SetupWindow(w)
        return w

##################################
# Canvas Handlers
##################################

class DblMicroscopePanelXmlHandler(xrc.XmlResourceHandler):
    def __init__(self):
        xrc.XmlResourceHandler.__init__(self)
        # Specify the styles recognized by objects of this type
        self.AddStyle("wxTAB_TRAVERSAL", wx.TAB_TRAVERSAL)
        self.AddWindowStyles()

    # This method and the next one are required for XmlResourceHandlers
    def CanHandle(self, node):
        capable = self.IsOfClass(node, "DblMicroscopePanel")
        return capable

    def DoCreateResource(self):
        assert self.GetInstance() is None

        # Now create the object
        panel = mscp.DblMicroscopePanel(self.GetParentAsWindow(),
                                        id=self.GetID(),
                                        pos=self.GetPosition(),
                                        size=self.GetSize(),
                                        style=self.GetStyle())
        self.SetupWindow(panel)
        return panel

HANDLER_CLASS_LIST = [
                      CustomStreamPanelEntryXmlHandler,
                      DblMicroscopePanelXmlHandler,
                      FixedStreamPanelEntryXmlHandler,
                      FoldPanelBarXmlHandler,
                      FoldPanelItemXmlHandler,
                      GenBitmapButtonHandler,
                      ImageButtonHandler,
                      ImageTextButtonHandler,
                      ImageTextToggleButtonHandler,
                      PopupImageButtonHandler,
                      SuggestTextCtrlHandler,
                      TabButtonButtonHandler,
                      UnitIntegerCtrlHandler,
                      ]

