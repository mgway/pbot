from connection import Connection
import config
import log
import commands

from collections import defaultdict
import imp
import os
import sys
import time
import traceback

os.stat_float_times(False)
commands_mtime = os.stat('commands.py').st_mtime

class BotStates:
	DISCONNECTED = 0
	CONNECTING = 1
	REGISTERING = 2 # server has accepted our host/nick/user
	UNIDENTIFIED = 3
	IDENTIFYING = 4
	IDENTIFIED = 5
STATE = BotStates()

class ServerMessage:
	''' nick, command, target, text '''
	def __init__(self, line):
		self.nick = None
		if line[0] == ':':
			split = line[1:].split(' ', 3)
			source = split.pop(0)
			exclaim = source.find('!')
			if exclaim != -1:
				self.nick = source[:exclaim]
		else:
			split = line.split(' ', 2)

		self.command = split[0]
		self.target = split[1]
		self.text = None
		if len(split) > 2 and split[2]:
			self.text = split[2]
			if self.text[0] == ':':
				self.text = self.text[1:]

		self.line = line

	def __str__(self):
		return 'ServerMessage(%r)' % self.line

class Bot:
	def __init__(self, config):
		self.config = config
		self.state = STATE.DISCONNECTED
		self.conn = None
		self.last_recv = None
		self.awaiting_pong = False

		self.handlers = {
			'PING': self.handle_ping,
			'376': self.handle_motd, # RPL_ENDOFMOTD
			'422': self.handle_motd, # ERR_NOMOTD
			'NOTICE': self.handle_notice,
			'MODE': self.handle_mode,
			'PRIVMSG': self.handle_privmsg,
		}

		self.scripts = defaultdict(list)

	def __str__(self):
		return '<Bot: %s/%s>' % (self.config.host, self.config.nick)

	def exception(self, line):
		exc_type, exc_value, exc_tb = sys.exc_info()
		exc_list = traceback.format_exception(exc_type, exc_value, exc_tb)
		self.log(line + '\n' + ''.join(exc_list))

		path, lineno, method, code = traceback.extract_tb(exc_tb)[-1]
		path = os.path.relpath(path)
		exc_name = exc_type.__name__
		notice = '%s:%s():%d %s: %s' % (path, method, lineno, exc_name, exc_value)
		if code is not None:
			notice += ' | ' + code[:50]
		self.notice(config.settings['owner'], notice)

	def log(self, text):
		log.write('%s/%s: %s' % (self.config.host, self.config.nick, text))

	def connect(self):
		host = self.config.host
		port = self.config.port
		self.log('connecting to port %d...' % port)
		self.last_recv = time.time()
		self.awaiting_pong = False
		if not self.conn:
			self.conn = Connection()
		fd, error = self.conn.connect(host, port)
		if error:
			self.log(error)
			self.state = STATE.DISCONNECTED
		else:
			self.state = STATE.CONNECTING
		return fd

	def handle(self):
		for line in self.conn.recv():
			msg = ServerMessage(line)
			handler = self.handlers.get(msg.command)
			if handler:
				try:
					handler(msg)
				except:
					self.exception(line)
		self.last_recv = time.time()

	def check_disconnect(self, ts):
		time_since = ts - self.last_recv
		ping_timeout_wait = config.PING_INTERVAL + config.PING_TIMEOUT
		if time_since > ping_timeout_wait:
			self.log('no reply from server in %ds' % ping_timeout_wait)
			self.disconnect()
			return True
		elif time_since > config.PING_INTERVAL and not self.awaiting_pong and self.state == STATE.IDENTIFIED:
			# don't let the server's reply to ping reset last_recv unless we're fully
			# identified lest we get stuck forever in a partially-connected state
			self.ping()

	def nick(self, new_nick):
		self.conn.send('nick', new_nick)

	def join(self, channel):
		self.conn.send('JOIN', channel)

	def say(self, target, message):
		self.conn.send('PRIVMSG', target, ':'+message)

	def notice(self, target, message):
		self.conn.send('NOTICE', target, ':'+message)

	def ctcp_reply(self, target, *args):
		self.notice(target, '%c%s%c' % (1, ' '.join(args), 1))

	def ping(self):
		self.conn.send('PING', 'pbot')
		self.awaiting_pong = True

	def disconnect(self):
		self.log('disconnecting')
		self.conn.disconnect()
		self.state = STATE.DISCONNECTED

	def __join_channels(self):
		self.log('autojoining channels...')
		for c in self.config.channels:
			self.join(c)

	def handle_ping(self, msg):
		self.conn.send('PONG', msg.target)

	def handle_motd(self, msg):
		self.state = STATE.UNIDENTIFIED
		if self.config.nickserv is None:
			self.state = STATE.IDENTIFIED
			self.__join_channels()

	def handle_notice(self, msg):
		if self.state < STATE.REGISTERING:
			self.nick(self.config.nick)
			self.conn.send('USER', self.config.user, 'pbot', 'pbot', ':'+self.config.user)
			self.state = STATE.REGISTERING
		elif self.state == STATE.UNIDENTIFIED:
			if self.config.nickserv is None:
				self.state = STATE.IDENTIFIED
				self.__join_channels()
			elif msg.nick and msg.nick.upper() == 'NICKSERV':
				self.say(msg.nick, 'IDENTIFY ' + self.config.nickserv)
				self.state = STATE.IDENTIFYING

	def handle_mode(self, msg):
		if msg.target == self.config.nick:
			if msg.text == '+r':
				self.state = STATE.IDENTIFIED
				self.log('nickserv accepted identification')
				self.__join_channels()

	def handle_privmsg(self, msg):
		if msg.text[0] == chr(1) and len(msg.text) > 2 and msg.text[-1] == chr(1):
			self.handle_ctcp(msg)
			return

		if msg.target != self.config.nick:
			if msg.text[0] == '!' and len(msg.text) > 1:
				split = msg.text[1:].split(' ', 1)
				command = split[0]
				text = ''
				if len(split) > 1:
					text = split[1]

				if config.settings['autoreload']:
					global commands_mtime
					new_mtime = os.stat('commands.py').st_mtime
					if new_mtime > commands_mtime:
						imp.reload(commands)
						commands_mtime = new_mtime
				handler = commands.handlers.get(command)
				if handler:
					handler(self, msg.target, msg.nick, command, text)
			elif msg.text.startswith('>>> '):
				commands.python_inline(self, msg)
			elif msg.text.startswith('>>>>'):
				if len(msg.text) == 4:
					commands.python_multiline(self, msg)
					del self.scripts[msg.nick]
				elif msg.text[4] == ' ':
					if len(self.scripts[msg.nick]) >= 16:
						bot.say(msg.target, '%s: script cannot be longer than 16 lines' % msg.nick)
					else:
						self.scripts[msg.nick].append(msg.text[5:])
			else:
				commands.youtube(self, msg)

	def handle_ctcp(self, msg):
		if msg.target == self.config.nick:
			split = msg.text[1:-1].split(' ', 1)
			command = split[0]
			if command == 'VERSION':
				self.ctcp_reply(msg.nick, 'VERSION', 'pbot https://github.com/raylu/pbot')
