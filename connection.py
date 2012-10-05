import socket

socket.setdefaulttimeout(180)

class Connection:
	def __init__(self):
		self.socket = None
		self.last_buf = None
		self.debug = False

	def send(self, *data):
		line = ' '.join(data) + '\r\n'
		if self.debug: print('->', line, end='')
		self.socket.send(bytes(line, 'utf-8'))

	def recv(self):
		data = self.socket.recv(4096)
		if self.last_buf is not None:
			data = self.last_buf + data
			self.last_buf = None
		lines = data.split(b'\r\n')
		for i in range(len(lines) - 1):
			line = str(lines[i], 'utf-8', 'replace')
			if self.debug: print('<-', line)
			yield line
		last = lines[-1]
		if last:
			self.last_buf = last

	def connect(self, host, port, nick, user):
		self.socket = socket.create_connection((host, port))
		self.send('NICK', nick)
		self.send('USER', user, 'pbot', 'pbot', ':'+user)

	def disconnect(self):
		if self.socket is None:
			return
		self.send('QUIT')
		self.socket.recv(1024)
		self.socket.close()
		self.socket = None
