#!/usr/bin/env python
# confPanel.py -- Presents torrc with syntax highlighting.
# Released under the GPL v3 (http://www.gnu.org/licenses/gpl.html)

import math
import curses
import socket

from TorCtl import TorCtl
from util import panel, uiTools

# torrc parameters that can be defined multiple times without overwriting
# from src/or/config.c (entries with LINELIST or LINELIST_S)
# last updated for tor version 0.2.1.19
MULTI_LINE_PARAM = ["AlternateBridgeAuthority", "AlternateDirAuthority", "AlternateHSAuthority", "AuthDirBadDir", "AuthDirBadExit", "AuthDirInvalid", "AuthDirReject", "Bridge", "ControlListenAddress", "ControlSocket", "DirListenAddress", "DirPolicy", "DirServer", "DNSListenAddress", "ExitPolicy", "HashedControlPassword", "HiddenServiceDir", "HiddenServiceOptions", "HiddenServicePort", "HiddenServiceVersion", "HiddenServiceAuthorizeClient", "HidServAuth", "Log", "MapAddress", "NatdListenAddress", "NodeFamily", "ORListenAddress", "ReachableAddresses", "ReachableDirAddresses", "ReachableORAddresses", "RecommendedVersions", "RecommendedClientVersions", "RecommendedServerVersions", "SocksListenAddress", "SocksPolicy", "TransListenAddress", "__HashedControlSessionPassword"]

# hidden service options need to be fetched with HiddenServiceOptions
HIDDEN_SERVICE_PARAM = ["HiddenServiceDir", "HiddenServiceOptions", "HiddenServicePort", "HiddenServiceVersion", "HiddenServiceAuthorizeClient"]
HIDDEN_SERVICE_FETCH_PARAM = "HiddenServiceOptions"

# size modifiers allowed by config.c
LABEL_KB = ["kb", "kbyte", "kbytes", "kilobyte", "kilobytes"]
LABEL_MB = ["m", "mb", "mbyte", "mbytes", "megabyte", "megabytes"]
LABEL_GB = ["gb", "gbyte", "gbytes", "gigabyte", "gigabytes"]
LABEL_TB = ["tb", "terabyte", "terabytes"]

# time modifiers allowed by config.c
LABEL_MIN = ["minute", "minutes"]
LABEL_HOUR = ["hour", "hours"]
LABEL_DAY = ["day", "days"]
LABEL_WEEK = ["week", "weeks"]

class ConfPanel(panel.Panel):
  """
  Presents torrc with syntax highlighting in a scroll-able area.
  """
  
  def __init__(self, confLocation, conn, logPanel):
    panel.Panel.__init__(self, -1)
    self.confLocation = confLocation
    self.showLineNum = True
    self.stripComments = False
    self.confContents = []
    self.scroll = 0
    
    # lines that don't matter due to duplicates
    self.irrelevantLines = []
    
    # used to check consistency with tor's actual values - corrections mapping
    # is of line numbers (one-indexed) to tor's actual values
    self.corrections = {}
    self.conn = conn
    self.logger = logPanel
    
    self.reset()
  
  def reset(self, logErrors=True):
    """
    Reloads torrc contents and resets scroll height. Returns True if
    successful, else false.
    """
    
    try:
      resetSuccessful = True
      
      confFile = open(self.confLocation, "r")
      self.confContents = confFile.readlines()
      confFile.close()
      
      # checks if torrc differs from get_option data
      self.irrelevantLines = []
      self.corrections = {}
      parsedCommands = {}       # mapping of parsed commands to line numbers
      
      for lineNumber in range(len(self.confContents)):
        lineText = self.confContents[lineNumber].strip()
        
        if lineText and lineText[0] != "#":
          # relevant to tor (not blank nor comment)
          ctlEnd = lineText.find(" ")   # end of command
          argEnd = lineText.find("#")   # end of argument (start of comment or end of line)
          if argEnd == -1: argEnd = len(lineText)
          command, argument = lineText[:ctlEnd], lineText[ctlEnd:argEnd].strip()
          
          # expands value if it's a size or time
          comp = argument.strip().lower().split(" ")
          if len(comp) > 1:
            size = 0
            if comp[1] in LABEL_KB: size = int(comp[0]) * 1024
            elif comp[1] in LABEL_MB: size = int(comp[0]) * 1048576
            elif comp[1] in LABEL_GB: size = int(comp[0]) * 1073741824
            elif comp[1] in LABEL_TB: size = int(comp[0]) * 1099511627776
            elif comp[1] in LABEL_MIN: size = int(comp[0]) * 60
            elif comp[1] in LABEL_HOUR: size = int(comp[0]) * 3600
            elif comp[1] in LABEL_DAY: size = int(comp[0]) * 86400
            elif comp[1] in LABEL_WEEK: size = int(comp[0]) * 604800
            if size != 0: argument = str(size)
              
          # most parameters are overwritten if defined multiple times, if so
          # it's erased from corrections and noted as duplicate instead
          if not command in MULTI_LINE_PARAM and command in parsedCommands.keys():
            previousLineNum = parsedCommands[command]
            self.irrelevantLines.append(previousLineNum)
            if previousLineNum in self.corrections.keys(): del self.corrections[previousLineNum]
          
          parsedCommands[command] = lineNumber + 1
          
          # check validity against tor's actual state
          try:
            actualValues = []
            if command in HIDDEN_SERVICE_PARAM:
              # hidden services are fetched via a special command
              hsInfo = self.conn.get_option(HIDDEN_SERVICE_FETCH_PARAM)
              for entry in hsInfo:
                if entry[0] == command:
                  actualValues.append(entry[1])
                  break
            else:
              # general case - fetch all valid values
              for key, val in self.conn.get_option(command):
                # TODO: check for a better way of figuring out CSV parameters
                # (kinda doubt this is right... in config.c its listed as being
                # a 'LINELIST') - still, good enough for common cases
                if command in MULTI_LINE_PARAM: toAdd = val.split(",")
                else: toAdd = [val]
                
                for newVal in toAdd:
                  newVal = newVal.strip()
                  if newVal not in actualValues: actualValues.append(newVal)
            
            # there might be multiple values on a single line - if so, check each
            if command in MULTI_LINE_PARAM and "," in argument:
              arguments = []
              for entry in argument.split(","):
                arguments.append(entry.strip())
            else:
              arguments = [argument]
            
            for entry in arguments:
              if not entry in actualValues:
                self.corrections[lineNumber + 1] = ", ".join(actualValues)
          except (TypeError, socket.error, TorCtl.ErrorReply, TorCtl.TorCtlClosed):
            # TODO: for some reason the above provided:
            # TypeError: sequence item 0: expected string, NoneType found
            # 
            # for the corrections setting. This issue seems to be specific to
            # Gentoo, OpenSuse, and OpenBSD but haven't yet managed to
            # reproduce. Catching the TypeError to just drop the torrc
            # validation for those systems
            
            if logErrors: self.logger.monitor_event("WARN", "Unable to validate torrc")
      
      # logs issues that arose
      if self.irrelevantLines and logErrors:
        if len(self.irrelevantLines) > 1: first, second, third = "Entries", "are", ", including lines"
        else: first, second, third = "Entry", "is", " on line"
        baseMsg = "%s in your torrc %s ignored due to duplication%s" % (first, second, third)
        
        self.logger.monitor_event("NOTICE", "%s: %s (highlighted in blue)" % (baseMsg, ", ".join([str(val) for val in self.irrelevantLines])))
      
      if self.corrections and logErrors:
        self.logger.monitor_event("WARN", "Tor's state differs from loaded torrc")
    except IOError, exc:
      resetSuccessful = False
      self.confContents = ["### Unable to load torrc ###"]
      if logErrors: self.logger.monitor_event("WARN", "Unable to load torrc (%s)" % str(exc))
    
    self.scroll = 0
    return resetSuccessful
  
  def handleKey(self, key):
    self._resetBounds()
    pageHeight = self.maxY - 1
    if key == curses.KEY_UP: self.scroll = max(self.scroll - 1, 0)
    elif key == curses.KEY_DOWN: self.scroll = max(0, min(self.scroll + 1, len(self.confContents) - pageHeight))
    elif key == curses.KEY_PPAGE: self.scroll = max(self.scroll - pageHeight, 0)
    elif key == curses.KEY_NPAGE: self.scroll = max(0, min(self.scroll + pageHeight, len(self.confContents) - pageHeight))
    elif key == ord('n') or key == ord('N'): self.showLineNum = not self.showLineNum
    elif key == ord('s') or key == ord('S'):
      self.stripComments = not self.stripComments
      self.scroll = 0
    self.redraw()
  
  def draw(self):
    self.addstr(0, 0, "Tor Config (%s):" % self.confLocation, uiTools.LABEL_ATTR)
    
    pageHeight = self.maxY - 1
    numFieldWidth = int(math.log10(len(self.confContents))) + 1
    lineNum, displayLineNum = self.scroll + 1, 1 # lineNum corresponds to torrc, displayLineNum concerns what's presented
    
    # determine the ending line in the display (prevents us from going to the 
    # effort of displaying lines that aren't visible - isn't really a 
    # noticeable improvement unless the torrc is bazaarly long) 
    if not self.stripComments:
      endingLine = min(len(self.confContents), self.scroll + pageHeight)
    else:
      # checks for the last line of displayable content (ie, non-comment)
      endingLine = self.scroll
      displayedLines = 0        # number of lines of content
      for i in range(self.scroll, len(self.confContents)):
        endingLine += 1
        lineText = self.confContents[i].strip()
        
        if lineText and lineText[0] != "#":
          displayedLines += 1
          if displayedLines == pageHeight: break
    
    for i in range(self.scroll, endingLine):
      lineText = self.confContents[i].strip()
      skipLine = False # true if we're not presenting line due to stripping
      
      command, argument, correction, comment = "", "", "", ""
      commandColor, argumentColor, correctionColor, commentColor = "green", "cyan", "cyan", "white"
      
      if not lineText:
        # no text
        if self.stripComments: skipLine = True
      elif lineText[0] == "#":
        # whole line is commented out
        comment = lineText
        if self.stripComments: skipLine = True
      else:
        # parse out command, argument, and possible comment
        ctlEnd = lineText.find(" ")   # end of command
        argEnd = lineText.find("#")   # end of argument (start of comment or end of line)
        if argEnd == -1: argEnd = len(lineText)
        
        command, argument, comment = lineText[:ctlEnd], lineText[ctlEnd:argEnd], lineText[argEnd:]
        if self.stripComments: comment = ""
        
        # changes presentation if value's incorrect or irrelevant
        if lineNum in self.corrections.keys():
          argumentColor = "red"
          correction = " (%s)" % self.corrections[lineNum]
        elif lineNum in self.irrelevantLines:
          commandColor = "blue"
          argumentColor = "blue"
      
      if not skipLine:
        numOffset = 0     # offset for line numbering
        if self.showLineNum:
          self.addstr(displayLineNum, 0, ("%%%ii" % numFieldWidth) % lineNum, curses.A_BOLD | uiTools.getColor("yellow"))
          numOffset = numFieldWidth + 1
        
        xLoc = 0
        displayLineNum, xLoc = self.addstr_wrap(displayLineNum, xLoc, command, curses.A_BOLD | uiTools.getColor(commandColor), numOffset)
        displayLineNum, xLoc = self.addstr_wrap(displayLineNum, xLoc, argument, curses.A_BOLD | uiTools.getColor(argumentColor), numOffset)
        displayLineNum, xLoc = self.addstr_wrap(displayLineNum, xLoc, correction, curses.A_BOLD | uiTools.getColor(correctionColor), numOffset)
        displayLineNum, xLoc = self.addstr_wrap(displayLineNum, xLoc, comment, uiTools.getColor(commentColor), numOffset)
        
        displayLineNum += 1
      
      lineNum += 1

