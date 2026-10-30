#!/usr/bin/env python3
"""Model of the recovered factory `.esd` grammar and export key order.

Independent reimplementation of the parts of the saved `AppDevelopment.jar`
that decide whether a generated engineering screen parses and what the factory
export element would poll. It exists so a payload can be checked on the host
before anything is written to the unit. It models only `screen`, `keyValue`,
`export` and `script`; any other element fails closed.

Recovered from unit 9411, HN+R_US_AU_K0942_3 / MU 1038; the bytecode evidence
is recorded in docs/cluster-map/performance-per3-sweep.md. This models the
parser, not the renderer: a payload that passes here is still unproven on the
target.
"""
# TokenReader.tokenizeLine builds a StreamTokenizer, calls resetSyntax, then
# quoteChar('"'), slashSlashComments(true), wordChars(40, 122) and
# commentChar('#'). Tokens whose sval is null are skipped by the caller, so
# every character outside the word range and outside quotes is a separator.
WORD_FIRST, WORD_LAST = 40, 122
QUOTE = '"'
COMMENT = '#'
# ScreenElementModel modelType/dataType values taken from the saved classes.
MODELTYPE_KEYVALUE = 3
MODELTYPE_SCRIPT = 7
MODELTYPE_EXPORT = 9
DATATYPE_INT = 0
DATATYPE_STRING = 1
# ElementParser.getDataSource: "per" is 0 and "sys" is 1; anything else throws.
DATA_SOURCES = {'per': 0, 'sys': 1}


class EsdError(ValueError):
    """Parse refusal, worded without caller paths or firmware text."""


def tokenize_line(text):
    """Word runs and quoted strings, in order; separators are dropped.

    Note that '/' is inside the word range, so it is consumed as an ordinary
    word character and the configured '//' comment never takes effect.
    """
    tokens, index, length = [], 0, len(text)
    while index < length:
        char = text[index]
        if char == COMMENT:
            break
        if char == QUOTE:
            index += 1
            start = index
            while index < length and text[index] != QUOTE:
                index += 1
            tokens.append(text[start:index])
            # An unterminated quote ends at end of line, as StreamTokenizer does.
            index += 1
            continue
        if WORD_FIRST <= ord(char) <= WORD_LAST:
            start = index
            while index < length and WORD_FIRST <= ord(text[index]) <= WORD_LAST:
                index += 1
            tokens.append(text[start:index])
            continue
        index += 1
    return tokens


class Element:
    def __init__(self, model_type):
        self.model_type = model_type
        self.label = None
        self.namespace = 0
        self.data_source = None
        self.data_type = None
        self.keys = []
        self.refresh_interval = 0
        self.factor = None
        self.format = None
        self.export_screen = None
        self.export_scope = None
        self.function_call = None


class Screen:
    def __init__(self, screen_id, name, parent):
        self.screen_id = screen_id
        self.name = name
        self.parent = parent
        self.elements = []
        self.children = []


def _decode(text, bits):
    """Integer.decode / Long.decode, bounded to a signed Java width."""
    try:
        value = int(text, 0)
    except ValueError:
        raise EsdError('value parameter is not a number')
    if not -(2 ** (bits - 1)) <= value < 2 ** (bits - 1):
        raise EsdError('value parameter does not fit its Java width')
    return value


def _parse_keyvalue(lines, at):
    """KeyvalueParser: value is required; label, poll and format are optional."""
    element = Element(MODELTYPE_KEYVALUE)
    while at < len(lines):
        tokens = tokenize_line(lines[at])
        if not tokens:
            at += 1
            continue
        head = tokens[0].lower()
        if head == 'value':
            if len(tokens) != 5:
                raise EsdError('wrong number of arguments for value')
            if tokens[1] == 'int':
                element.data_type = DATATYPE_INT
            elif tokens[1] == 'String':
                element.data_type = DATATYPE_STRING
            else:
                # Neither branch is taken, so no key is bound and the element
                # fails its required-field check further down.
                raise EsdError('value type must be exactly "int" or "String"')
            if tokens[2] not in DATA_SOURCES:
                raise EsdError('invalid data source specified')
            element.data_source = DATA_SOURCES[tokens[2]]
            element.namespace = _decode(tokens[3], 32)
            element.keys = [_decode(tokens[4], 64)]
        elif head == 'label':
            if len(tokens) != 2:
                raise EsdError('wrong number of arguments for LABEL')
            element.label = tokens[1]
        elif head == 'poll':
            if len(tokens) != 2:
                raise EsdError('wrong number of arguments for POLL')
            element.refresh_interval = _decode(tokens[1], 32)
        elif head == 'format':
            if len(tokens) != 3:
                raise EsdError('wrong number of arguments for FORMAT')
            try:
                element.factor = float(tokens[1])
            except ValueError:
                raise EsdError('FORMAT is not a number')
            element.format = tokens[2]
        else:
            break
        at += 1
    if element.label is None or not element.keys:
        raise EsdError('required KEYVALUE elements missing')
    return element, at


def _parse_export(lines, at):
    """ExportParser: label, screen and scope, each with one argument."""
    element = Element(MODELTYPE_EXPORT)
    while at < len(lines):
        tokens = tokenize_line(lines[at])
        if not tokens:
            at += 1
            continue
        head = tokens[0].lower()
        if head in ('label', 'screen', 'scope'):
            if len(tokens) != 2:
                raise EsdError('wrong number of arguments for %s'
                               % head.upper())
            setattr(element, {'label': 'label', 'screen': 'export_screen',
                              'scope': 'export_scope'}[head], tokens[1])
        else:
            break
        at += 1
    if element.label is None or element.export_screen is None:
        raise EsdError('required EXPORT element missing')
    return element, at


def _parse_script(lines, at):
    """ScriptParser: value (source, namespace, key, string) and label are
    required; poll is optional.

    ScriptModel.performAction stores the string through the data source; for
    `sys 1 0x0100` SystemAccessor runs it as a shell command (see
    docs/wifi-client/wifi-client-mode.md, "The engineering-screen button").
    """
    element = Element(MODELTYPE_SCRIPT)
    while at < len(lines):
        tokens = tokenize_line(lines[at])
        if not tokens:
            at += 1
            continue
        head = tokens[0].lower()
        if head == 'value':
            if len(tokens) != 5:
                raise EsdError('wrong number of arguments for value')
            if tokens[1] not in DATA_SOURCES:
                raise EsdError('invalid data source specified')
            element.data_source = DATA_SOURCES[tokens[1]]
            element.namespace = _decode(tokens[2], 32)
            element.keys = [_decode(tokens[3], 64)]
            element.function_call = tokens[4]
        elif head == 'label':
            if len(tokens) != 2:
                raise EsdError('wrong number of arguments for LABEL')
            element.label = tokens[1]
        elif head == 'poll':
            if len(tokens) != 2:
                raise EsdError('wrong number of arguments for POLL')
            element.refresh_interval = _decode(tokens[1], 32)
        else:
            break
        at += 1
    if element.label is None or not element.keys:
        raise EsdError('required BUTTON elements missing')
    return element, at


ELEMENTS = {'keyvalue': _parse_keyvalue, 'export': _parse_export,
            'script': _parse_script}


def parse(text):
    """Parses one .esd body into screens, in declaration order.

    Mirrors ScreenParser: names are lowercased, ids are assigned in order, a
    screen named "main" becomes the entry screen, and every other screen is
    linked under its declared parent.
    """
    lines = text.splitlines()
    screens, by_name, main, at = [], {}, None, 0
    current = None
    while at < len(lines):
        tokens = tokenize_line(lines[at])
        if not tokens:
            at += 1
            continue
        head = tokens[0].lower()
        if head == 'screen':
            if len(tokens) != 3:
                raise EsdError('a screen declaration needs three tokens')
            name, parent = tokens[1].lower(), tokens[2].lower()
            if name in by_name:
                raise EsdError('duplicate screen name')
            current = Screen(len(screens), name, parent)
            screens.append(current)
            by_name[name] = current
            if name == 'main':
                main = current
            at += 1
            continue
        if head in ELEMENTS:
            if current is None:
                raise EsdError('an element appeared before any screen')
            element, at = ELEMENTS[head](lines, at + 1)
            current.elements.append(element)
            continue
        raise EsdError('unsupported element: this model covers screen, '
                       'keyValue, export and script only')
    for screen in screens:
        if screen is main:
            continue
        parent = by_name.get(screen.parent)
        if parent is not None:
            parent.children.append(screen)
    return screens, by_name, main


def parse_files(bodies):
    """Parses several .esd files and links screens across them.

    Every factory file declares exactly one screen, so this mirrors how the
    GEM parses a directory. `bodies` maps file name to text; children are
    appended in the iteration order given, but the real
    `File.listFiles(FilenameFilter)` order is unspecified, so cross-file
    sibling order must not be relied on.
    """
    screens, by_name, main = [], {}, None
    for name in bodies:
        parsed, local, local_main = parse(bodies[name])
        for screen in parsed:
            if screen.name in by_name:
                raise EsdError('duplicate screen name across files')
            screen.screen_id = len(screens)
            screens.append(screen)
            by_name[screen.name] = screen
        if local_main is not None:
            if main is not None:
                raise EsdError('more than one main screen')
            main = local_main
    for screen in screens:
        screen.children = []
    for screen in screens:
        if screen is main:
            continue
        parent = by_name.get(screen.parent)
        if parent is not None:
            parent.children.append(screen)
    return screens, by_name, main


def export_order(by_name, screen_name):
    """ScreenParser.getScreens: the named screen, then its subtree in order.

    The stored `scope` is never read by the export path, so the walk always
    recurses.
    """
    ordered = []

    def walk(name):
        screen = by_name.get(name)
        if screen is None:
            return
        ordered.append(screen)
        for child in screen.children:
            walk(child.name)

    walk(screen_name.lower())
    return ordered


def export_keys(by_name, screen_name):
    """(label, namespace, key) in the order ExportModel$1 would poll them.

    Elements contribute one entry per bound key; export elements hold a
    zero-length key array and contribute nothing.
    """
    collected = []
    for screen in export_order(by_name, screen_name):
        for element in screen.elements:
            for key in element.keys:
                collected.append((element.label, element.namespace, key))
    return collected
