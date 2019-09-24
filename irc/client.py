# -*- coding: utf-8 -*-

from . import features
from . import events
import logging
import socket
import _thread
import time
import re
import textwrap


logger = logging.getLogger('mustached-ironman')
_rfc_1459_command_regexp = re.compile("^(:(?P<prefix>[^ ]+) +)?" +
    "(?P<command>[^ ]+)( *(?P<argument> .+))?")


class ClientPool(object):
    def __init__(self):
        self.nocheck = False
        self.clients = []

    def server(self, bot):
        client = IRCConnection(bot)
        self.clients.append(client)
        return client

    def process_forever(self):
        connected = True
        while connected:
            connected = False
            for client in self.clients:
                if client.connected or self.nocheck or client.nocheck:
                    connected = True
                # client.process_data()
            time.sleep(0.1)


class IRCConnection(object):
    def __init__(self, bot):
        self.connected = False
        self.features = features.FeatureSet()
        self.queue = []
        self.handlers = {}
        self.socket = False
        self.core = bot
        self.reconncount = 0
        self.nocheck = False
        self.whoing = False

        # Handlers internos
        # meh, the ping-pong is optional!
        #self.addhandler("ping", self._ping_ponger)
        self.addhandler("join", self._on_join)
        self.addhandler("part", self._on_part)
        self.addhandler("nick", self._on_nick)
        self.addhandler("mode", self._on_mode)
        self.addhandler("nicknameinuse", self._changenick)
        self.addhandler("banlist", self._on_banlist)
        self.addhandler("kick", self._on_kick)
        self.addhandler("quit", self._on_quit)
        self.addhandler("currenttopic", self._currtopic)
        self.addhandler("whospcrpl", self._whoreply)
        self.addhandler("whoreply", self._normalwhoreply)
        self.addhandler("enfofwho", self._endofwho)
        self.addhandler("330", self._whoisaccount)

    def connect(self, server, port, nick, user, realname,
            msgdelay=0.5, reconnects=10):
        self.reconncount = 0
        if self.connected:
            self.disconnect("Changing servers")

        self.channels = {}
        self.buffer = LineBuffer()
        self.nickname = nick
        self.server = server
        self.port = port
        self.username = user
        self.gecos = realname
        self.msgdelay = msgdelay
        self.maxreconnect = reconnects
        logger.info("Conectando a {0}:{1}".format(server, port))
        try:
            self.socket = socket.create_connection((server, port))
        except socket.error as err:
            logger.error("No se pudo conectar a {0}:{1}: {2}"
                .format(server, port, err))
            return 1
        # Comenzamos a procesar la cola..
        _thread.start_new_thread(self.process_queue, ())
        self.connected = True
        self._handle_event(Event("connect", None, None))
        time.sleep(1)  # Le damos tiempo de actuar a los handlers del ircv3
        # Nos registramos...
        self.user(user, realname)
        self.nick(nick, True)
        _thread.start_new_thread(self.process_forever, ())

    def reconnect(self):
        self.nocheck = True
        self.connect(self.server, self.port, self.nickname, self.username,
                    self.gecos, self.msgdelay, self.maxreconnect)
        self.nocheck = False

    def process_forever(self):
        while self.connected:
            self.process_data()
        self.reconncount += 1
        if self.reconncount <= self.maxreconnect:
            self.reconnect()

    def process_queue(self):
        try:  # Guh, este thread no debe morir!
            while True:
                if self.connected is False:
                    return 0
                for stuff in self.queue:
                    time.sleep(self.msgdelay)
                    self.send_stuff(stuff)
                self.queue = []
                time.sleep(self.msgdelay)
        except:
            pass

    def process_data(self):
        if not self.connected:
            return 1
        try:
            reader = getattr(self.socket, 'read', self.socket.recv)
            new_data = reader(2 ** 14)
        except socket.error:
            # The server hung up.
            self.disconnect("Connection reset by peer")
            return False
        if not new_data:
            # Read nothing: connection must be down.
            self.disconnect("Connection reset by peer")
            return False

        self.buffer.feed(new_data)

        for line in self.buffer:
            if not line:
                continue
            logger.debug(line)
            self._processline(line)

    def _currtopic(self, connection, event):
        try:
            self.channels[event.arguments[0]]
        except:
            self.channels[event.arguments[0]] = Channel(event.arguments[0])
        self.channels[event.arguments[0]].topic = event.arguments[1]

    # 31 = Add user
    def _whoreply(self, connection, ev):
        if ev.arguments[0] != "31":
            return 0
        self.channels[self.whoing[0]].adduser(User(ev.arguments[5],
                                         ev.arguments[2], ev.arguments[3],
                                        ev.arguments[8], ev.arguments[4],
                                        ev.arguments[7], ev.arguments[6],
                                        self))

    def _endofwho(self, connection, ev):
        self.whoing = False

    def _normalwhoreply(self, connection, ev):
        if self.whoing is False:
            return
        if ev.arguments[0] == self.whoing[0] or \
                                             ev.arguments[4] == self.whoing[1]:
            self.channels[self.whoing[0]].adduser(User(ev.arguments[4],
                                    ev.arguments[1], ev.arguments[2],
                                    ev.arguments[6], ev.arguments[3],
                                    None, ev.arguments[5], self), True)
            try:
                self.features.whox
            except:
                for i in self.channels:
                    i = self.channels[i]
                    l = i.getuser(ev.arguments[4])
                    if l is not False:
                        if l.account is not None:
                            self.channels[self.whoing[0]].getuser(
                                ev.arguments[4]).account = l.account
                            return
                self.whois([ev.arguments[4]])

    def _changenick(self, connection, event):
        self.nickname = self.nickname + "_"
        self.nick(self.nickname, True)

    def _whoisaccount(self, connection, event):
        for i in self.channels:
            i = self.channels[i]
            l = i.getuser(event.arguments[0])
            print(l)
            if l is not False:
                l.account = event.arguments[1]

    def _on_join(self, connection, event):
        if parse_nick(event.source)[1] == self.nickname:
            self.channels[event.target] = Channel(event.target)
            # [0] = #channel, [1] = target
            self.whoing = [event.target, event.target]
            try:
                self.features.whox
                self.who(event.target, "%tcnuhrsaf,31")
            except:
                self.who(event.target)
            self.mode(event.target, "b")
        else:
            self.whoing = [event.target, parse_nick(event.source)[1]]
            try:
                self.features.whox
                self.who(parse_nick(event.source)[1], "%tcnuhrsaf,31")
            except:
                self.who(parse_nick(event.source)[1])

    def _on_nick(self, connection, event):
        if parse_nick(event.source)[1] == self.nickname:
            self.nickname = event.target
        for i in self.channels:
            i = self.channels[i]
            l = i.getuser(parse_nick(event.source)[1])
            if l is not False:
                i.renameuser(parse_nick(event.source)[1], event.target)

    def _on_banlist(self, connection, event):
        self.channels[event.arguments[0]].addban(event.arguments[1])

    def _on_quit(self, connection, event):
        for i in self.channels:
            i = self.channels[i]
            l = i.getuser(parse_nick(event.source)[1])
            if l is not False:
                i.deluser(l)
        if parse_nick(event.source)[1] == self.nickname:
            self.channels = {}

    def getuser(self, nick):  # Heh, a bit expensive, no?
        for i in self.channels:
            i = self.channels[i]
            l = i.getuser(nick)
            if l is not False:
                return l
        return False

    def _on_mode(self, connection, event):
        l = self.separateModes(event.arguments)
        for i in l:
            for q in self.features.prefix:
                if i[0] == ("+" + self.features.prefix[q]):
                    self.channels[event.target].getuser(i[1]).modifyPrefix(q)
                    return
                elif i[0] == ("-" + self.features.prefix[q]):
                    self.channels[event.target].getuser(i[1]).modifyPrefix(q,
                                                                         False)
                    return
            if i[0] == "+b":
                self.channels[event.target].addban(i[1])
            elif i[0] == "-b":
                self.channels[event.target].delban(i[1])

    def _on_kick(self, connection, event):
        if parse_nick(event.source)[1] == self.nickname:
            try:
                self.channels[event.target]
                #self.join(event.target)  # >:D
            except:
                pass
        else:
            self.channels[event.target].deluser(
                    self.channels[event.target].getuser(event.target))

    def _on_part(self, connection, event):
        if parse_nick(event.source)[1] == self.nickname:
            try:
                self.channels[event.target]
                self.join(event.target)  # >:D
            except:
                pass
        else:
            self.channels[event.target].deluser(
                    self.channels[event.target].getuser(event.target))

    #from limnoria
    def separateModes(self, args):
        """Separates modelines into single mode change tuples.  Basically, you
        should give it the .args of a MODE IrcMsg.

        Examples:

        >>> separateModes(['+ooo', 'jemfinch', 'StoneTable', 'philmes'])
        [('+o', 'jemfinch'), ('+o', 'StoneTable'), ('+o', 'philmes')]

        >>> separateModes(['+o-o', 'jemfinch', 'PeterB'])
        [('+o', 'jemfinch'), ('-o', 'PeterB')]

        >>> separateModes(['+s-o', 'test'])
        [('+s', None), ('-o', 'test')]

        >>> separateModes(['+sntl', '100'])
        [('+s', None), ('+n', None), ('+t', None), ('+l', 100)]
        """
        if not args:
            return []
        modes = args[0]
        assert modes[0] in '+-', 'Invalid args: %r' % args
        args = list(args[1:])
        ret = []
        for c in modes:
            if c in '+-':
                last = c
            else:
                if last == '+':
                    #requireArguments = _plusRequireArguments
                    requireArguments = self.features.chanmodes[0] + \
                                        self.features.chanmodes[1] + \
                                        self.features.chanmodes[2]
                else:
                    requireArguments = self.features.chanmodes[0]
                for l in self.features.prefix:
                    requireArguments += self.features.prefix[l]

                if c in requireArguments:
                    if not args:
                        # It happens, for example with "MODE #channel +b", which
                        # is used for getting the list of all bans.
                        continue
                    arg = args.pop(0)
                    try:
                        arg = int(arg)
                    except ValueError:
                        pass
                    ret.append((last + c, arg))
                else:
                    ret.append((last + c, None))
        return ret

    def _ping_ponger(self, connection, event):
        "A global handler for the 'ping' event"
        connection.pong(event.target)

    def parsemode(self, mode, ev, remove=False):
        res = []
        cmodelist = self.features.chanmodes
        param = cmodelist[0] + cmodelist[1] + cmodelist[2]
        for i, val in enumerate(self.features.prefix):
            param = param + self.features.prefix[val]
        pos = 0
        for c in ev.arguments[0]:
            if c == "-":
                rving = True
                pass
            elif c == "+":
                rving = False
            else:
                if c in param:
                    pos = pos + 1
            if rving is False and remove is not False:
                continue
            elif rving is True and remove is not True:
                continue

            if c == mode:
                res.append(ev.arguments[pos])  # BEEP BEEP BEEP BEEP
        return res

    def _handle_event(self, event):
        if event.type == "ping":
            self._ping_ponger(self, event)
        try:
            for handler in self.handlers[event.type]:
                _thread.start_new_thread(handler, (self, event))
        except:
            pass

    def _processline(self, line):
        prefix = None
        command = None
        arguments = None
        self._handle_event(Event("all_raw_messages",
                                 self.server,
                                 None,
                                 [line]))

        m = _rfc_1459_command_regexp.match(line)
        if m.group("prefix"):
            prefix = m.group("prefix")

        if m.group("command"):
            command = m.group("command").lower()

        if m.group("argument"):
            a = m.group("argument").split(" :", 1)
            arguments = a[0].split()
            if len(a) == 2:
                arguments.append(a[1])

        # Translate numerics into more readable strings.
        command = events.numeric.get(command, command)

        if command == "nick":
            if NickMask(prefix).nick == self.real_nickname:
                self.real_nickname = arguments[0]
        elif command == "welcome":
            # Record the nickname in case the client changed nick
            # in a nicknameinuse callback.
            self.real_nickname = arguments[0]
        elif command == "featurelist":
            self.features.load(arguments)

        if command in ["privmsg", "notice"]:
            target, message = arguments[0], arguments[1]
            messages = _ctcp_dequote(message)

            if command == "privmsg":
                if is_channel(target):
                    command = "pubmsg"
            else:
                if is_channel(target):
                    command = "pubnotice"
                else:
                    command = "privnotice"

            for m in messages:
                if isinstance(m, tuple):
                    if command in ["privmsg", "pubmsg"]:
                        command = "ctcp"
                    else:
                        command = "ctcpreply"

                    m = list(m)
                    logger.debug("command: %s, source: %s, target: %s, "
                        "arguments: %s", command, prefix, target, m)
                    self._handle_event(Event(command, NickMask(prefix), target,
                         m))
                    if command == "ctcp" and m[0] == "ACTION":
                        self._handle_event(Event("action", prefix, target,
                             m[1:]))
                else:
                    logger.debug("command: %s, source: %s, target: %s, "
                        "arguments: %s", command, prefix, target, [m])
                    self._handle_event(Event(command, NickMask(prefix), target,
                        [m]))
        else:
            target = None

            if command == "quit":
                arguments = [arguments[0]]
            elif command == "ping":
                target = arguments[0]
            else:
                target = arguments[0]
                arguments = arguments[1:]

            if command == "mode":
                if not is_channel(target):
                    command = "umode"

            logger.debug("command: %s, source: %s, target: %s, "
                "arguments: %s", command, prefix, target, arguments)
            self._handle_event(Event(command, NickMask(prefix), target,
                arguments))

    def addhandler(self, message, function, vip=False):
        if vip is False:
            try:
                self.handlers[message].append(function)
            except:
                self.handlers[message] = []
                self.handlers[message].append(function)
        else:
            try:
                self.handlers[message].insert(0, function)
            except:
                self.handlers[message] = []
                self.handlers[message].insert(0, function)
        logger.debug("Se ha añadido un handler para '{0}'".format(message))
        return [len(self.handlers[message]), message]

    def delhandler(self, identif):
        del self.handlers[identif[1]][identif[0] - 1]

    def disconnect(self, message):
        self.reconncount = 100000  # :D
        if not self.connected:
            return

        self.connected = False

        self.quit(message)

        try:
            self.socket.shutdown(socket.SHUT_WR)
            self.socket.close()
        except socket.error:
            pass
        del self.socket
        self._handle_event(Event("disconnect", self.server, "", [message]))

    def send(self, raw, urgent=False):
        if urgent is False:
            self.queue.append(raw)
        else:
            self.send_stuff(raw)

    def send_stuff(self, stuff):
        stuff = stuff.replace("\n", "")
        bytes_ = stuff.encode('utf-8') + b'\r\n'
        if len(bytes_) > 512:
            logger.warning("Se ha intentado enviar un mensaje muy largo!")
        try:
            self.socket.send(bytes_)
            logger.debug("TO SERVER: {0}".format(stuff))
        except socket.error:
            # Ouch!
            self.disconnect("Connection reset by peer.")

    def msg(self, target, message, nonewmsg=False):
        if not is_channel(target):
            self.notice(target, message, nonewmsg)
            return

        if self.core.readConf("channel.notices", chan=target,
                                                        default=True) is False:
            self.privmsg(target, message, nonewmsg)
        else:
            self.notice(target, message, nonewmsg)

    # TODO: Toooodos los mensajes que se puedan enviar...
    def quit(self, message="Bye", urgent=True):
        self.send("QUIT :{0}".format(message), urgent)

    def user(self, user, realname):
        self.send("USER {0} * * :{1}".format(user, realname), True)

    def nick(self, nick, urgent=False):
        self.send("NICK {0}".format(nick), urgent)

    def pong(self, sstr=""):
        self.send("PONG :{0}".format(sstr))

    def who(self, target="", op=""):
        self.send("WHO%s%s" % (target and (" " + target), op and (" " + op)))

    def join(self, *channels):
        for channel in channels:
            self.send("JOIN {0}".format(channel))

    def part(self, channel, msg):
        del self.channels[channel]
        self.send("PART {0} :{1}".format(channel, msg))

    def privmsg(self, target, msg, nonewmsg=False):
        maxlen = 440 - len("NOTICE {0} :".format(target.encode('utf-8')))
        if len(msg.encode('utf-8')) > maxlen:
            words = msg.split(" ")
            avail = maxlen
            footer = " …"
            result = ['']
            k = 0
            for word in words:
                word += " "
                if len(word.encode('utf-8')) > maxlen:
                    while len(word.encode('utf-8')) > avail: # ?!
                        # Palabra mas larga que el limite!? Cortar la palabra
                        result[k] += word[:-maxlen]
                        word = word[maxlen:]
                        result[k] += footer
                        result.append("")
                        k += 1
                
                if len(word.encode('utf-8')) > avail:
                    result[k] += footer
                    result.append("")
                    k += 1
                    avail = maxlen
                    
                result[k] += word
                avail = avail - len(word.encode('utf-8'))
            
            for msg in result:
                self._privmsg(target, msg)
        else:
            self._privmsg(target, msg)

    def _privmsg(self, target, text):
        """Send a PRIVMSG command."""
        self.send("PRIVMSG %s :%s" % (target, text))

    def cap(self, subcommand, *args):
        """
        Send a CAP command according to `the spec
        <http://ircv3.atheme.org/specification/capability-negotiation-3.1>`_.

        Arguments:

            subcommand -- LS, LIST, REQ, ACK, CLEAR, END
            args -- capabilities, if required for given subcommand

        Example:

            .cap('LS')
            .cap('REQ', 'multi-prefix', 'sasl')
            .cap('END')
        """
        cap_subcommands = set('LS LIST REQ ACK NAK CLEAR END'.split())
        client_subcommands = set(cap_subcommands) - set('NAK')
        assert subcommand in client_subcommands, "invalid subcommand"

        def _multi_parameter(args):
            """
            According to the spec::

                If more than one capability is named, the RFC1459 designated
                sentinel (:) for a multi-parameter argument must be present.

            It's not obvious where the sentinel should be present or if it must
            be omitted for a single parameter, so follow convention and only
            include the sentinel prefixed to the first parameter if more than
            one parameter is present.
            """
            if len(args) > 1:
                return (':' + args[0],) + args[1:]
            return args

        args = _multi_parameter(args)
        self.send(' '.join(('CAP', subcommand) + args))

    def ctcp(self, ctcptype, target, parameter=""):
        """Send a CTCP command."""
        ctcptype = ctcptype.upper()
        self.privmsg(target, "\001%s%s\001" % (ctcptype, parameter and
                                                (" " + parameter) or ""))

    def ctcp_reply(self, target, parameter):
        """Send a CTCP REPLY command."""
        self.notice(target, "\001%s\001" % parameter)

    def kick(self, channel, nick, comment=""):
        """Send a KICK command."""
        self.send("KICK %s %s%s" % (channel, nick, (comment and
                                                        (" :" + comment))))

    def globops(self, text):
        """Send a GLOBOPS command."""
        self.send("GLOBOPS :" + text)

    def info(self, server=""):
        """Send an INFO command."""
        self.send_raw(" ".join(["INFO", server]).strip())

    def invite(self, nick, channel):
        """Send an INVITE command."""
        self.send(" ".join(["INVITE", nick, channel]).strip())

    def ison(self, nicks):
        """Send an ISON command.

        Arguments:

            nicks -- List of nicks.
        """
        self.send("ISON " + " ".join(nicks))

    def squit(self, server, comment=""):
        """Send an SQUIT command."""
        self.send("SQUIT %s%s" % (server, comment and (" :" + comment)))

    def stats(self, statstype, server=""):
        """Send a STATS command."""
        self.send("STATS %s%s" % (statstype, server and (" " + server)))

    def time(self, server=""):
        """Send a TIME command."""
        self.send("TIME" + (server and (" " + server)))

    def topic(self, channel, new_topic=None):
        """Send a TOPIC command."""
        if new_topic is None:
            self.send("TOPIC " + channel)
        else:
            self.send("TOPIC %s :%s" % (channel, new_topic))

    def trace(self, target=""):
        """Send a TRACE command."""
        self.send("TRACE" + (target and (" " + target)))

    def userhost(self, nicks):
        """Send a USERHOST command."""
        self.send("USERHOST " + ",".join(nicks))

    def users(self, server=""):
        """Send a USERS command."""
        self.send("USERS" + (server and (" " + server)))

    def version(self, server=""):
        """Send a VERSION command."""
        self.send("VERSION" + (server and (" " + server)))

    def wallops(self, text):
        """Send a WALLOPS command."""
        self.send("WALLOPS :" + text)

    def whois(self, targets):
        """Send a WHOIS command."""
        self.send("WHOIS " + ",".join(targets))

    def whowas(self, nick, max="", server=""):
        """Send a WHOWAS command."""
        self.send("WHOWAS %s%s%s" % (nick,
                                         max and (" " + max),
                                         server and (" " + server)))

    def links(self, remote_server="", server_mask=""):
        """Send a LINKS command."""
        command = "LINKS"
        if remote_server:
            command = command + " " + remote_server
        if server_mask:
            command = command + " " + server_mask
        self.send(command)

    def list(self, channels=None, server=""):
        """Send a LIST command."""
        command = "LIST"
        if channels:
            command = command + " " + ",".join(channels)
        if server:
            command = command + " " + server
        self.send(command)

    def lusers(self, server=""):
        """Send a LUSERS command."""
        self.send("LUSERS" + (server and (" " + server)))

    def mode(self, target, command):
        """Send a MODE command."""
        self.send("MODE %s %s" % (target, command))

    def motd(self, server=""):
        """Send an MOTD command."""
        self.send("MOTD" + (server and (" " + server)))

    def names(self, channels=None):
        """Send a NAMES command."""
        self.send("NAMES" + (channels and (" " + ",".join(channels)) or ""))

    def notice(self, target, msg, nonewmsg=False):
        maxlen = 440 - len("NOTICE {0} :".format(target.encode('utf-8')))
        if len(msg.encode('utf-8')) > maxlen:
            words = msg.split()
            avail = maxlen
            footer = " …"
            result = ['']
            k = 0
            for word in words:
                word += " "
                if len(word.encode('utf-8')) > maxlen:
                    while len(word.encode('utf-8')) > avail: # ?!
                        # Palabra mas larga que el limite!? Cortar la palabra
                        result[k] += word[:-maxlen]
                        word = word[maxlen:]
                        result[k] += footer
                        result.append("")
                        k += 1
                
                if len(word.encode('utf-8')) > avail:
                    result[k] += footer
                    result.append("")
                    k += 1
                    avail = maxlen
                    
                result[k] += word
                avail = avail - len(word.encode('utf-8'))
            
            for msg in result:
                self._notice(target, msg)
        else:
            self._notice(target, msg)

    def _notice(self, target, text):
        """Send a NOTICE command."""
        # Should limit len(text) here!
        self.send("NOTICE %s :%s" % (target, text))

    def oper(self, nick, password):
        """Send an OPER command."""
        self.send("OPER %s %s" % (nick, password))


class Event(object):
    def __init__(self, type, source, target, arguments=None):
        self.type = type
        self.source = source
        self.source2 = source
        self.target = target
        if arguments is None:
            arguments = []
        self.arguments = arguments
        if type == "privmsg" or type == "pubmsg" or type == "ctcpreply" or type\
        == "ctcp" or type == "pubnotice" or type == "privnotice":
            if not is_channel(target):
                self.target = parse_nick(source)[1]
            if not is_channel(source):
                self.source = parse_nick(source)[1]
            self.splitd = arguments[0].split()


class NickMask(str):
    @classmethod
    def from_params(cls, nick, user, host):
        return cls('{nick}!{user}@{host}'.format(**vars()))

    @property
    def nick(self):
        return self.split("!")[0]

    @property
    def userhost(self):
        return self.split("!")[1]

    @property
    def host(self):
        return self.split("@")[1]

    @property
    def user(self):
        return self.userhost.split("@")[0]


class LineBuffer(object):
    line_sep_exp = re.compile(b'\r?\n')

    def __init__(self):
        self.buffer = b''

    def feed(self, byte):
        self.buffer += byte

    encoding = 'utf-8'
    errors = 'replace'

    def lines(self):
        return (line.decode(self.encoding, self.errors)
            for line in self._lines())

    def _lines(self):
        lines = self.line_sep_exp.split(self.buffer)
        # save the last, unfinished, possibly empty line
        self.buffer = lines.pop()
        return iter(lines)

    def __iter__(self):
        return self.lines()

    def __len__(self):
        return len(self.buffer)


class Channel(object):
    def __init__(self, channel, topic="", modes=""):
        self.name = channel
        self.topic = topic
        self.modes = modes
        self.users = {}
        self.banlist = []

    def addban(self, ban):
        if not ban in self.banlist:
            self.banlist.append(ban)

    def delban(self, ban):
        if ban in self.banlist:
            self.banlist.remove(ban)

    def adduser(self, user, normalwho=False):
        try:
            if normalwho is False:
                self.users[user.nickname] = user
            else:
                try:
                    # Con who si el usuario existe solo añadimos el servidor
                    self.users[user.nickname].server = user.server
                except:
                    self.users[user.nickname] = user
        except:
            pass

    def getuser(self, nick):
        try:
            return self.users[nick]
        except:
            return False

    def renameuser(self, oldnick, newnick):
        try:
            self.users[newnick] = self.users[oldnick]
            del self.users[oldnick]
        except:
            pass

    def deluser(self, user):
        try:
            del self.users[user.nickname]
        except:
            pass


class User(object):
    account = None
    server = None
    nickname = None
    username = None
    host = None

    def __init__(self, nickname, username, host, gecos, server, account, stats,
                                                                        cli):
        self.nickname = nickname
        self.username = username
        self.host = host
        self.realname = gecos
        self.server = server
        if account != "0":
            self.account = account
        self.cli = cli
        self.processPrefix(stats)
        self.stats = stats

    def processPrefix(self, stats):
        oprefixes = ""
        for i in self.cli.features.prefix:
            oprefixes += i
        if stats[0] == "G":
            self.away = True
        else:
            self.away = False
        stats = stats[1:]
        self.is_op = False
        self.is_voiced = False
        for i in stats:
            if i not in oprefixes:
                continue
            if i != "" and i != "+":
                self.is_op = True
            elif i == "+":
                self.is_voiced = True

    def modifyPrefix(self, prefix, add=True):
        if add is True:
            if prefix not in self.stats:
                self.stats += prefix
        else:
            if prefix in self.stats:
                self.stats = self.stats.replace(prefix, "")
        self.processPrefix(self.stats)

    def isVoiced(self, op=False):
        if op is True and self.is_op is True:
            return True
        elif self.is_voiced is True:
            return True


def is_channel(string):
    """Check if a string is a channel name.

    Returns true if the argument is a channel name, otherwise false.
    """
    return string and string[0] in "#&+!"


def parse_nick(name):
    """ parse a nickname and return a tuple of (nick, mode, user, host)

    <nick> [ '!' [<mode> = ] <user> ] [ '@' <host> ]
    """

    try:
        nick, rest = name.split('!')
    except ValueError:
        return (name, None, None, None)
    try:
        mode, rest = rest.split('=')
    except ValueError:
        mode, rest = None, rest
    try:
        user, host = rest.split('@')
    except ValueError:
        return (name, mode, rest, None)

    return (name, nick, mode, user, host)

_LOW_LEVEL_QUOTE = "\020"
_CTCP_LEVEL_QUOTE = "\134"
_CTCP_DELIMITER = "\001"
_low_level_mapping = {
    "0": "\000",
    "n": "\n",
    "r": "\r",
    _LOW_LEVEL_QUOTE: _LOW_LEVEL_QUOTE
}

_low_level_regexp = re.compile(_LOW_LEVEL_QUOTE + "(.)")


def _ctcp_dequote(message):
    """[Internal] Dequote a message according to CTCP specifications.

    The function returns a list where each element can be either a
    string (normal message) or a tuple of one or two strings (tagged
    messages).  If a tuple has only one element (ie is a singleton),
    that element is the tag; otherwise the tuple has two elements: the
    tag and the data.

    Arguments:

        message -- The message to be decoded.
    """

    def _low_level_replace(match_obj):
        ch = match_obj.group(1)

        # If low_level_mapping doesn't have the character as key, we
        # should just return the character.
        return _low_level_mapping.get(ch, ch)

    if _LOW_LEVEL_QUOTE in message:
        # Yup, there was a quote.  Release the dequoter, man!
        message = _low_level_regexp.sub(_low_level_replace, message)

    if _CTCP_DELIMITER not in message:
        return [message]
    else:
        # Split it into parts.  (Does any IRC client actually *use*
        # CTCP stacking like this?)
        chunks = message.split(_CTCP_DELIMITER)

        messages = []
        i = 0
        while i < len(chunks) - 1:
            # Add message if it's non-empty.
            if len(chunks[i]) > 0:
                messages.append(chunks[i])

            if i < len(chunks) - 2:
                # Aye!  CTCP tagged data ahead!
                messages.append(tuple(chunks[i + 1].split(" ", 1)))

            i = i + 2

        if len(chunks) % 2 == 0:
            # Hey, a lonely _CTCP_DELIMITER at the end!  This means
            # that the last chunk, including the delimiter, is a
            # normal message!  (This is according to the CTCP
            # specification.)
            messages.append(_CTCP_DELIMITER + chunks[-1])

        return messages
