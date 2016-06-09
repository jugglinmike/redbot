#!/usr/bin/env python

"""
The Resource Expert Droid header checks.

process_headers() will process a list of (key, val) tuples.
"""


import calendar
from email.utils import parsedate as lib_parsedate
import re
import sys
import unittest
import urllib

from redbot.message import http_syntax as syntax
from redbot.formatter import f_num
import redbot.speak as rs
from ._decorators import *

# base URLs for references
rfc2616 = "http://tools.ietf.org/html/rfc2616.html#%s"
rfc5988 = "http://tools.ietf.org/html/rfc5988.html#section-5"
rfc6265 = "http://tools.ietf.org/html/rfc6265.html#%s"
rfc6266 = "http://tools.ietf.org/html/rfc6266.html#section-4"

### configuration
MAX_HDR_SIZE = 4 * 1024
MAX_TTL_HDR = 8 * 1000

# map of header name aliases, lowercase-normalised
header_aliases = {
    'x-pad-for-netscrape-bug': 'x-pad',
    'xx-pad': 'x-pad',
    'x-browseralignment': 'x-pad',
    'nncoection': 'connectiox',
    'cneonction': 'connectiox',
    'yyyyyyyyyy': 'connectiox',
    'xxxxxxxxxx': 'connectiox',
    'x_cnection': 'connectiox',
    '_onnection': 'connectiox',
}


def process_headers(msg):
    """
    Parse and check the message for obvious syntactic errors,
    as well as semantic errors that are self-contained (i.e.,
    it can be determined without examining other headers, etc.).

    Using msg.headers, it populates:
      - .headers with a Unicode version of the input
      - .parsed_headers with a dictionary of parsed header values
    """

    hdr_dict = {}
    header_block_size = len(msg.version)
    if msg.is_request:
        header_block_size += len(msg.method) + len(msg.uri) + 2
    else:
        header_block_size += len(msg.status_phrase) + 5
    clean_hdrs = []      # unicode version of the header tuples
    parsed_hdrs = {}     # dictionary of parsed header values
    offset = 0
    for name, value in msg.headers:
        offset += 1
        subject = "offset-%s" % offset
        hdr_size = len(name) + len(value)
        if hdr_size > MAX_HDR_SIZE:
            msg.add_note(subject, rs.HEADER_TOO_LARGE,
               header_name=name, header_size=f_num(hdr_size))
        header_block_size += hdr_size
        
        # decode the header to make it unicode clean
        try:
            name = name.decode('ascii', 'strict')
        except UnicodeError:
            name = name.decode('ascii', 'ignore')
            msg.add_note(subject, rs.HEADER_NAME_ENCODING,
                header_name=name)
        try:
            value = value.decode('ascii', 'strict')
        except UnicodeError:
            value = value.decode('iso-8859-1', 'replace')
            msg.add_note(subject, rs.HEADER_VALUE_ENCODING,
                header_name=name)
        clean_hdrs.append((name, value))
        msg.set_context(field_name=name)
        
        # check field name syntax
        if not re.match("^\s*%s\s*$" % syntax.TOKEN, name, re.VERBOSE):
            msg.add_note(subject, rs.FIELD_NAME_BAD_SYNTAX)
            continue

        norm_name = name.lower()
        value = value.strip()
        
        hdr_parse = load_header_func(norm_name, 'parse')
        if hdr_parse:
            if hasattr(hdr_parse, 'pre_parse'):
                values = hdr_parse.pre_parse(value)
            else:
                values = [value]
            for value in values:
                if not hdr_dict.has_key(norm_name):
                    hdr_dict[norm_name] = (name, [])
                parsed_value = hdr_parse(subject, value, msg)
                if parsed_value != None:
                    hdr_dict[norm_name][1].append(parsed_value)
        
    # replace the original header tuple with ones that are clean unicode
    msg.headers = clean_hdrs

    # join parsed header values
    for norm_name, (orig_name, values) in hdr_dict.items():
        msg.set_context(field_name=orig_name)
        hdr_join = load_header_func(norm_name, 'join')
        if hdr_join:
            subject = "header-%s" % norm_name
            joined_value = hdr_join(subject, values, msg)
            if joined_value == None:
                continue
            parsed_hdrs[norm_name] = joined_value
    msg.parsed_headers = parsed_hdrs

    # check the total header block size
    if header_block_size > MAX_TTL_HDR:
        msg.add_note('header', rs.HEADER_BLOCK_TOO_LARGE,
            header_block_size=f_num(header_block_size))


def load_header_func(header_name, func=None):
    """
    Return a header parser for the given field name. If function isn't specified, 
    just return the module.
    """
    name_token = header_name.replace('-', '_').lower().encode('ascii', 'ignore')
    # anything starting with an underscore won't match
    if name_token[0] == '_':
        return
    # TODO: aliases
    try:
        module_name = "redbot.message.headers.%s" % name_token
        __import__(module_name)
        hdr_module = sys.modules[module_name]
    except (ImportError, KeyError, TypeError):
        return # we don't recognise the header.
    if func == None:
        return hdr_module
    try:
        return getattr(hdr_module, func)
    except AttributeError:
        return # we can't find the requested function.


def parse_date(value):
    """Parse a HTTP date. Raises ValueError if it's bad."""
    if not re.match(r"%s$" % syntax.DATE, value, re.VERBOSE):
        raise ValueError
    date_tuple = lib_parsedate(value)
    if date_tuple is None:
        raise ValueError
    # http://sourceforge.net/tracker/index.php?func=detail&aid=1194222&group_id=5470&atid=105470
    if date_tuple[0] < 100:
        if date_tuple[0] > 68:
            date_tuple = (date_tuple[0]+1900,)+date_tuple[1:]
        else:
            date_tuple = (date_tuple[0]+2000,)+date_tuple[1:]
    date = calendar.timegm(date_tuple)
    return date

def unquote_string(instr):
    """
    Unquote a unicode string; does NOT unquote control characters.

    @param instr: string to be unquoted
    @type instr: unicode
    @return: unquoted string
    @rtype: unicode
    """
    instr = unicode(instr).strip()
    if not instr or instr == '*':
        return instr
    if instr[0] == instr[-1] == '"':
        ninstr = instr[1:-1]
        instr = re.sub(r'\\(.)', r'\1', ninstr)
    return instr

def split_string(instr, item, split):
    """
    Split instr as a list of items separated by splits.

    @param instr: string to be split
    @param item: regex for item to be split out
    @param split: regex for splitter
    @return: list of strings
    """
    if not instr:
        return []
    return [h.strip() for h in re.findall(
        r'%s(?=%s|\s*$)' % (item, split), instr
    )]

def parse_params(msg, subject, instr, nostar=None, delim=";"):
    """
    Parse parameters into a dictionary.

    @param msg: the message instance to use
    @param subject: the subject identifier
    @param instr: string to be parsed
    @param nostar: list of parameters that definitely don't get a star. If
                   True, no parameter can be starred.
    @param delim: delimter between params, default ";"
    @return: dictionary of {name: value}
    """
    param_dict = {}
    instr = instr.encode('ascii') # TODO: non-ascii input?
    for param in split_string(instr, syntax.PARAMETER, r"\s*%s\s*" % delim):
        try:
            key, val = param.split("=", 1)
        except ValueError:
            param_dict[param.lower()] = None
            continue
        k_norm = key.lower() # TODO: warn on upper-case in param?
        if param_dict.has_key(k_norm):
            msg.add_note(subject, rs.PARAM_REPEATS, param=k_norm)
        if val[0] == val[-1] == "'":
            msg.add_note(subject,
                rs.PARAM_SINGLE_QUOTED,
                param=k_norm,
                param_val=val,
                param_val_unquoted=val[1:-1]
            )
        if key[-1] == '*':
            if nostar is True or (nostar and k_norm[:-1] in nostar):
                msg.add_note(subject, rs.PARAM_STAR_BAD,
                                param=k_norm[:-1])
            else:
                if val[0] == '"' and val[-1] == '"':
                    msg.add_note(subject, rs.PARAM_STAR_QUOTED,
                                    param=k_norm)
                    val = unquote_string(val).encode('ascii')
                try:
                    enc, lang, esc_v = val.split("'", 3)
                except ValueError:
                    msg.add_note(subject, rs.PARAM_STAR_ERROR,
                                    param=k_norm)
                    continue
                enc = enc.lower()
                lang = lang.lower()
                if enc == '':
                    msg.add_note(subject,
                        rs.PARAM_STAR_NOCHARSET, param=k_norm)
                    continue
                elif enc not in ['utf-8']:
                    msg.add_note(subject,
                        rs.PARAM_STAR_CHARSET,
                        param=k_norm,
                        enc=enc
                    )
                    continue
                # TODO: catch unquoting errors, range of chars, charset
                unq_v = urllib.unquote(esc_v)
                dec_v = unq_v.decode(enc) # ok, because we limit enc above
                param_dict[k_norm] = dec_v
        else:
            param_dict[k_norm] = unquote_string(val)
    return param_dict



# TODO: allow testing of request headers
class HeaderTest(unittest.TestCase):
    """
    Testing machinery for headers.
    """
    name = None
    inputs = None
    expected_out = None
    expected_err = None

    def setUp(self):
        "Test setup."
        from redbot.message import DummyMsg
        self.msg = DummyMsg()

    def test_header(self):
        "Test the header."
        if not self.name:
            return self.skipTest('')
        self.msg.headers = [(self.name, inp) for inp in self.inputs]
        process_headers(self.msg)
        out = self.msg.parsed_headers.get(self.name.lower(), None)
        self.assertEqual(self.expected_out, out,
            "%s != %s" % (str(self.expected_out), str(out)))
        diff = set(
            [n.__name__ for n in self.expected_err]).symmetric_difference(
            set(self.msg.note_classes)
        )
        for msg in self.msg.notes: # check formatting
            msg.vars.update({'field_name': self.name, 'response': 'response'})
            self.assertTrue(msg.text % msg.vars)
            self.assertTrue(msg.summary % msg.vars)
        self.assertEqual(len(diff), 0, "Mismatched notes: %s" % diff)

