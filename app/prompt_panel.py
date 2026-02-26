#!/usr/bin/env python3
"""Native macOS floating panel with a WKWebView loading the prompt page."""

import sys
import signal
import webbrowser

from AppKit import (
    NSApplication,
    NSPanel,
    NSScreen,
    NSFloatingWindowLevel,
    NSTitledWindowMask,
    NSClosableWindowMask,
    NSApp,
    NSObject,
    NSColor,
)
from WebKit import WKWebView, WKWebViewConfiguration
from Foundation import NSURL, NSURLRequest, NSMakeRect
import objc


class ScriptHandler(NSObject):
    """Handle messages from JavaScript."""

    def userContentController_didReceiveScriptMessage_(self, controller, message):
        body = message.body()
        if isinstance(body, str) and body.startswith("openurl:"):
            # Open URL in default browser
            url = body[8:]
            webbrowser.open(url)
        else:
            # Close panel
            NSApp.terminate_(None)


class WindowDelegate(NSObject):
    def windowWillClose_(self, notification):
        NSApp.terminate_(None)


def main():
    url_str = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:5123/prompt"

    app = NSApplication.sharedApplication()

    width, height = 380, 460
    screen = NSScreen.mainScreen().frame()
    x = screen.origin.x + screen.size.width - width - 16
    y = screen.origin.y + screen.size.height - height - 36

    panel = NSPanel.alloc().initWithContentRect_styleMask_backing_defer_(
        NSMakeRect(x, y, width, height),
        NSTitledWindowMask | NSClosableWindowMask,
        2,
        False,
    )
    panel.setTitle_("Time Tracker")
    panel.setLevel_(NSFloatingWindowLevel)
    panel.setFloatingPanel_(True)
    panel.setBecomesKeyOnlyIfNeeded_(False)
    panel.setHidesOnDeactivate_(False)

    panel.setBackgroundColor_(NSColor.colorWithCalibratedRed_green_blue_alpha_(
        0.102, 0.102, 0.18, 1.0
    ))

    wk_config = WKWebViewConfiguration.alloc().init()
    handler = ScriptHandler.alloc().init()
    wk_config.userContentController().addScriptMessageHandler_name_(handler, "close")
    wk_config.userContentController().addScriptMessageHandler_name_(handler, "openurl")

    webView = WKWebView.alloc().initWithFrame_configuration_(
        NSMakeRect(0, 0, width, height),
        wk_config,
    )
    webView.setValue_forKey_(False, "drawsBackground")

    request = NSURLRequest.requestWithURL_(NSURL.URLWithString_(url_str))
    webView.loadRequest_(request)

    panel.setContentView_(webView)
    win_delegate = WindowDelegate.alloc().init()
    panel.setDelegate_(win_delegate)
    panel.makeKeyAndOrderFront_(None)
    app.activateIgnoringOtherApps_(True)

    signal.signal(signal.SIGTERM, lambda *_: NSApp.terminate_(None))
    app.run()


if __name__ == "__main__":
    main()
