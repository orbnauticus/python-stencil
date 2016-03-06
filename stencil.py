#!/usr/bin/env python

'''
A templating system for scripts which primarily output text.
'''

from __future__ import print_function

from six import StringIO, string_types

import re


class StencilBase(object):
    '''Base class for template engines.

    >>> class Web2pyStencil(StencilBase):
    ...   opener, closer = '{{', '}}'
    ...   indent = '  '
    >>> parse = Web2pyStencil.parse
    >>> parse("{{=123}}")
    'sys.stdout.write(str(123))\\n'

    >>> import sys

    >>> parse("123{{if True:}}456{{pass}}", out=sys.stdout)
    sys.stdout.write('123')
    if True:
      sys.stdout.write('456')

    >>> class ErbStencil(StencilBase):
    ...   opener, closer = '<%', '%>'
    >>> ErbStencil.parse("You have <%= 'no' if x==0 else x %> messages",
    ...                  out=sys.stdout)
    sys.stdout.write('You have ')
    sys.stdout.write(str('no' if x==0 else x))
    sys.stdout.write(' messages')

    >>> parse("Uncompleted substitution {{}")
    Traceback (most recent call last):
    ...
    SyntaxError: Unclosed substitution

    >>> parse("""This is a {{='self.multiline'
    ... }} message.""", out=sys.stdout)
    sys.stdout.write('This is a ')
    sys.stdout.write(str('self.multiline'))
    sys.stdout.write(' message.')

    >>> parse('{{="""This is a\\nself.multiline message."""}}', out=sys.stdout)
    sys.stdout.write(str("""This is a
    self.multiline message."""))

    Subs can contain whole blocks, but can't contain anything outside the sub.
    >>> parse('{{if more_complicated:\\n  it(should, work, anyway)\\n'
    ...       'else:\\n  exit(0)}}{{="This always prints"}}',
    ...       out=sys.stdout)
    if more_complicated:
      it(should, work, anyway)
    else:
      exit(0)
    sys.stdout.write(str("This always prints"))

    The 'pass' keyword takes on new meaning as a block-ending token by default
    >>> parse('{{pass}}')
    Traceback (most recent call last):
    ...
    SyntaxError: Got dedent outside of block

    Override the dedent method to change this behavior.

    Opening sequences aren't parsed inside substitutions
    >>> parse("{{='{{'}}")
    "sys.stdout.write(str('{{'))\\n"

    Closing sequences are, however.
    >>> parse("{{='Hello: }}'}}")
    Traceback (most recent call last):
    ...
    SyntaxError: EOL while scanning string literal

    One workaround is to concatenate strings containing the closing elements

    >>> parse("{{='Hello }''}'}}", out=sys.stdout)
    sys.stdout.write(str('Hello }''}'))
    '''

    writer = 'sys.stdout.write'
    indent = '\t'

    def __init__(self, path_or_file, filename='<string>'):
        if isinstance(path_or_file, string_types):
            path_or_file = open(path_or_file, 'r')
        self.file = path_or_file
        self.filename = getattr(path_or_file, 'name', filename)
        self.subs = {
            r'=\s*': self.on_equal_sign,
        }

    @staticmethod
    def dedent(token):
        '''
        Determine whether token should end the current block
        '''
        return token == 'pass'

    def on_equal_sign(self, _, remainder):
        '''
        Handler when a substitution begins with an equal sign
        '''
        return '%s(str(%s))' % (self.writer, remainder)

    @classmethod
    def parse(cls, data, out=None):
        '''
        Wrapper method for compiling a script directly from text
        '''
        self = cls(StringIO(data))
        return self.compile(out)

    def compile(self, out=None):
        '''
        Compile a stencil into a proper python script
        '''
        return_text = out is None
        if return_text:
            out = StringIO()
        Compiler(self, out).compile()
        if return_text:
            return out.getvalue()


class Compiler:
    '''
    Process a stencil
    '''
    def __init__(self, stencil, out):
        self.stencil = stencil
        self.out = out

        self.line = ''
        self.column = 0
        self.linenumber = 0
        self.level = 0
        self.multiline = ''
        self.currentline = ''

    def compile(self):
        '''
        Compile a stencil into a python script
        '''
        insub = False
        for self.linenumber, self.line in enumerate(self.stencil.file, 1):
            self.multiline, self.line, self.currentline = (
                '',
                self.multiline + self.line,
                self.line,
            )
            self.column = 0
            while self.line:
                delimiter = (self.stencil.closer if insub else
                             self.stencil.opener)
                text, found, self.line = self.line.partition(delimiter)
                if not found:
                    self.multiline += text
                    break
                if insub:
                    self.process_substitution(text)
                elif text:
                    self.print('%s(%r)' % (self.stencil.writer, text))
                self.column += len(text) + len(delimiter)
                insub = not insub
        if self.multiline:
            if insub:
                raise SyntaxError('Unclosed substitution', self.syntax_state())
            else:
                self.print('%s(%r)' % (self.stencil.writer, self.multiline))

    def process_substitution(self, text):
        '''
        Process content inside a substitution
        '''
        text = text.strip()
        if text:
            for pat, func in self.stencil.subs.items():
                match = re.match('(%s)' % pat, text)
                if match:
                    self.column += match.end()
                    text = text[match.end():]
                    self.print(func(*(match.groups() + (text,))))
                    break
            else:
                dedent = self.stencil.dedent(text)
                if dedent or any(text.startswith(b) for b in
                                 {'elif', 'else', 'except', 'finally'}):
                    self.level -= 1
                    if self.level < 0:
                        raise SyntaxError(
                            'Got dedent outside of block',
                            self.syntax_state())
                if not dedent:
                    self.print(text)
                if text[-1] == ':':
                    self.level += 1
            block_check = text
            if block_check[-1] == ':':
                # Add stubs to make line-by-line syntax checking
                #  work with blocks
                if block_check[:2] == 'el':
                    block_check = 'if 1:pass\n' + block_check
                elif (block_check.startswith('except') or
                      block_check.startswith('finally')):
                    block_check = 'try:pass\n' + block_check
                block_check += '\n\tpass'
            try:
                compile(block_check, self.stencil.filename, 'exec')
            except SyntaxError as error:
                raise SyntaxError(
                    error.msg, self.syntax_state(error.offset))

    def print(self, text):
        '''
        Write a statement to output with proper indentation
        '''
        print(self.stencil.indent * self.level, text, sep='', file=self.out)

    def syntax_state(self, offset=0):
        '''
        Return arguments for a SyntaxError
        '''
        return (
            self.stencil.filename,
            self.linenumber,
            self.column + offset,
            self.currentline,
        )


class Web2pyStencil(StencilBase):
    '''
    Stencil using syntax from web2py's template engine
    '''
    opener = '{{'
    closer = '}}'

    def __init__(self, *args, **kwargs):
        StencilBase.__init__(self, *args, **kwargs)
        self.subs[r'extend\s+'] = self.on_extend

    @staticmethod
    def on_extend(_, remainder):
        '''
        Handle extend keyword
        '''
        return '#Extending %s' % remainder


class ErbStencil(StencilBase):
    '''
    Stencil using syntax from erb template engine
    '''
    opener = '<%'
    closer = '%>'


if __name__ == '__main__':
    import doctest
    doctest.testmod()
