#!/usr/bin/env python
# Split and Parallel upload to Amazon AWS S3.


try:
	from cStringIO import StringIO
except ImportError:
	from StringIO import StringIO
from fnmatch import fnmatch
from gzip import GzipFile
from itertools import chain, imap, islice
import logging
from multiprocessing import JoinableQueue, Process, current_process
import multiprocessing
from optparse import OptionGroup, OptionParser
import os.path
import re
from ssl import SSLError
import sys
import tarfile
import time
import magic #python-magic
import mimetypes
from pprint import pprint as pp
import boto
from boto.s3.connection import S3Connection
from boto.s3.acl import CannedACLStrings
from boto.utils import compute_md5
e=sys.exit
from pprint import pprint

def put_update(bucket, key_name, value):
	key = bucket.get_key(key_name)
	if key is None:
		return bucket.new_key(key_name)
	else:
		# Boto's md5 function actually returns 3-tuple: (hexdigest, base64, size)
		value.calculate_md5()
		if key.etag == '"%s"' % value.md5[0]:
			return None
		else:
			return key


def statter(stat_queue, start, options):
	log = logging.getLogger(os.path.basename(sys.argv[0]))
	count, total_size = 0, 0
	while True:
		kwargs = stat_queue.get()
		if kwargs is None:
			stat_queue.task_done()
			break
		count += 1
		total_size += kwargs.get('size', 0)
		stat_queue.task_done()
	duration = time.time() - start
	log.info('put %d bytes in %d files in %.1f seconds (%d bytes/s, %.1f files/s)' % (total_size, count, duration, total_size / duration, count / duration))
	
class Value(object):
	def __init__(self, file_object_cache, content=None, filename=None, md5=None, offset=None, path=None, size=None, bucket_name=None):
		self.file_object_cache = file_object_cache
		self.content 	= content
		self.filename 	= filename
		self.bucket_name= bucket_name
		self.md5 	= md5
		self.offset = offset
		self.path 	= path
		self.size 	= size
		self.peek	= 1000
		self.pid	= current_process().pid

	def get_content(self):
		if self.content is None:
			if self.filename:
				if self.offset>=0 and self.size:

					with self.file_object_cache.open(self.filename) as file_object:
						#adjust offset and size
						if 1: #offset
							if not self.offset: # beginning of file
								pass
							else:
								file_object.seek(self.offset)
								pos = file_object.read(self.peek).find(os.linesep)
								assert pos>-1, 'Increase peek size to move offset to EOL.'
								print '[%d][%d]\tOFFSET: %s -> %s, \tSIZE: %s -> %s' % (self.pid, self.offset, self.offset, self.offset+pos, self.size, self.size-pos)

								self.offset=self.offset+pos
								self.size=self.size-pos
						if 1: #size
							file_object.seek(self.offset+self.size)
							contents=file_object.read(self.peek)
							if not contents: #EOF
								pass
							else:
								pos = contents.find(os.linesep)
								assert pos>-1, 'Increase peek size to move offset to EOL.'
								print '[%d][%d]\tsize: %s -> %s' % (self.pid,self.offset, self.size, self.size+pos)
								self.size=self.size+pos
						
						file_object.seek(self.offset)
						self.content = file_object.read(self.size)

			elif self.path:
				with open(self.path) as file_object:
					self.content = file_object.read()
			else:
				assert False
		return self.content
		
		
	def reset_both(self):
		
		if self.content is None:
			if self.filename:
				with self.file_object_cache.open(self.filename) as file_object:
					if not self.offset:
						pass
					else:
						file_object.seek(self.offset)
						pos = file_object.read(self.peek).find(os.linesep)
						assert pos>-1, 'Increase peek size to move offset to EOL.'
						print '[%d][%d]\tOFFSET: %s -> %s, \tSIZE: %s -> %s' % (self.pid, self.offset, self.offset, self.offset+pos, self.size, self.size-pos)

						self.offset=self.offset+pos
						self.size=self.size-pos
					if 1: #size
						file_object.seek(self.offset+self.size)
						contents=file_object.read(self.peek)
						if not contents: #EOF
							pass
						else:
							pos = contents.find(os.linesep)
							assert pos>-1, 'Increase peek size to move offset to EOL.'
							print '[%d][%d]\tsize: %s -> %s' % (self.pid,self.offset, self.size, self.size+pos)
							self.size=self.size+pos							
					
			else:
				pass
		else:
			pass

			
	def reset_offset(self):
		
		if self.content is None:
			if self.filename:
				with self.file_object_cache.open(self.filename) as file_object:
					if not self.offset:
						pass
					else:
						file_object.seek(self.offset)
						pos = file_object.read(self.peek).find(os.linesep)
						assert pos>-1, 'Increase peek size to move offset to EOL.'
						print '[%d][%d]\tOFFSET: %s -> %s, \tSIZE: %s -> %s' % (self.pid, self.offset, self.offset, self.offset+pos, self.size, self.size-pos)

						self.offset=self.offset+pos
						self.size=self.size-pos
					
			else:
				pass
		else:
			pass
	def reset_size(self):
		if self.content is None:
			if self.filename:
				with self.file_object_cache.open(self.filename) as file_object:
					file_object.seek(self.offset+self.size)
					contents=file_object.read(self.peek)
					if not contents: #EOF
						pass
					else:
						pos = contents.find(os.linesep)
						assert pos>-1, 'Increase peek size to move offset to EOL.'
						print '[%d][%d]\tsize: %s -> %s' % (self.pid,self.offset, self.size, self.size+pos)
						self.size=self.size+pos
					
			else:
				pass
		else:
			pass			
	def calculate_md5(self):
		if self.md5 is None:
			self.md5 = compute_md5(StringIO(self.get_content()))
		return self.md5

	def get_size(self):
		if self.size is None:
			if self.content:
				self.size = len(self.content)
			elif self.path:
				self.size = os.stat(self.path).st_size
			else:
				assert False
		return self.size

	def should_copy_content(self):
		return self.bucket_name is None

class FileObjectCache(object):

	def __init__(self):
		self.name = None
		self.file_object = None

	def open(self, name, *args):
		if name != self.name:
			self.name = name
			self.file_object = open(self.name, *args)
		return self

	def __enter__(self):
		return self.file_object

	def __exit__(self, exc_type, exc_value, traceback):
		pass
import math
chunk_size=10*10<<20

def walk_filesystem(source, options):
	if os.path.isdir(source):
		for dirpath, dirnames, filenames in os.walk(source):

			for filename in filenames:
				
				abs_path = os.path.join(dirpath, filename)

				rel_path = os.path.relpath(abs_path, source)
				basename=os.path.basename(rel_path)
				key_name = '/'.join([options.prefix,]+os.path.relpath(dirpath).split(os.sep) + [basename])
				if 1:
					fsize=float(os.stat(abs_path).st_size)
					print fsize,chunk_size, fsize>chunk_size
					if fsize>chunk_size:
						for x in range(int(math.ceil(fsize/chunk_size))):
							yield ('%s.%s' % (key_name,x), dict(filename=abs_path, offset=x*chunk_size, size=chunk_size))
					else:
						yield (key_name, dict(path=abs_path))
	elif os.path.isfile(source):

		key_name = os.path.normpath(os.path.join(options.prefix, source))
		print key_name
		fsize=float(os.stat(source).st_size)
		if fsize>chunk_size:
			for x in range(int(math.ceil(fsize/chunk_size))):
				new_key_name='%s.%s' % (key_name,x)
				print new_key_name
				yield (new_key_name, dict(filename=source, offset=x*chunk_size, size=chunk_size))
		else:
			
			yield (key_name, dict(path=source))


def repeatedly(func, *args, **kwargs):
	while True:
		yield func(*args, **kwargs)


def walker(walk, put_queue, sources, options):
	log = logging.getLogger(os.path.basename(sys.argv[0]))
	pairs = chain(*imap(lambda source: walk(source, options), sources))
	#print list(pairs)
	for pair in pairs:
		#print 111, pair
		put_queue.put(pair)
class Opt(object):
	def __init__(self):
		self.processes	= multiprocessing.cpu_count() * 4
		self.walk		= 'filesystem'
		self.prefix		= 'test'
		self.put		= 'update'
		self.gzip		= True
		self.secure		= True
		self.host		= 's3.amazonaws.com'
		self.bucket		= 'test-dev'
		self.headers	= None
		self.content_type = None
		self.dry_run	= False
		self.grant		= None
		self.encrypt_key= False
		self.quiet		= False
		self.verbose	= True
		self.lame_duck	= 0
		self.log_filename='/tmp/log.log'
options=Opt()
def putter(put, put_queue, stat_queue, options):
	pid=current_process().pid
	log = logging.getLogger(os.path.basename(sys.argv[0]))
	connection, bucket = None, None
	file_object_cache = FileObjectCache()


	if options.gzip:
		pass
		#log.debug('These content types will be gzipped: %s' % unicode(gzip_content_types))
	while True:
		args = put_queue.get()
		#print args, pid
		if args is None:
			put_queue.task_done()
			break
		key_name, value_kwargs = args
		#print(666,value_kwargs)
		if options.gzip:
			key_name = '%s.gz' %  key_name
		value = Value(file_object_cache, **value_kwargs)
		try:
			if connection is None:
				connection = S3Connection(is_secure=options.secure, host=options.host)
			if bucket is None:
				bucket = connection.get_bucket(options.bucket, validate=False)
			key = put(bucket, key_name, value)
			if key:
				if value.should_copy_content():
					if options.headers:
						headers = dict(tuple(header.split(':', 1)) for header in options.headers)
					else:
						headers = {}

					content = value.get_content()
					

					if options.gzip:
						headers['Content-Encoding'] = 'gzip'
						string_io = StringIO()
						gzip_file = GzipFile(compresslevel=1, fileobj=string_io, mode='w')
						gzip_file.write(content)
						gzip_file.close()
						content = string_io.getvalue()
						md5 = compute_md5(StringIO(content))
					if not options.dry_run:
						key.set_contents_from_string(content, headers, md5=md5, policy=options.grant, encrypt_key=options.encrypt_key)
				#log.info('%s %s> %s' % (value.path, 'z' if options.gzip else '-', key.name))
				stat_queue.put(dict(size=value.get_size()))
			else:
				log.info('skipping %s -> %s' % (value.path, key_name))
		except SSLError as exc:
			log.error('%s -> %s (%s)' % (value.path, key_name, exc))
			put_queue.put(args)
			connection, bucket = None, None
		except IOError as exc:
			log.error('%s -> %s (%s)' % (value.path, key_name, exc))
		put_queue.task_done()
		
def main(argv):
	logging.basicConfig(filename=options.log_filename, level=logging.INFO + 10 * (options.quiet - options.verbose))
	log = logging.getLogger(os.path.basename(sys.argv[0]))
	FORMAT = '%(asctime)s|%(levelname)s|%(process)d|%(module)s.py|%(funcName)s|%(lineno)d|  %(message)s'
	if 1:
		handler = logging.StreamHandler(sys.stdout)
		handler.setLevel(logging.DEBUG)
		formatter = logging.Formatter(FORMAT,datefmt="%Y-%m-%d %H:%M:%S")
		handler.setFormatter(formatter)
		#pprint(dir(handler))
		log.addHandler(handler)
	#log.info('test')
	#e()
	file_object_cache = FileObjectCache()
	#key_name, value_kwargs = args
	#value = Value(file_object_cache, content=None, filename=None, md5=None, offset=None, path=None, size=None, bucket_name=None)
	start = time.time()
	if 1:
		put_queue = JoinableQueue(1024 * options.processes)
		stat_queue = JoinableQueue()
		walk = {'filesystem': walk_filesystem}[options.walk]
		args=['/auto/fina-data/share/FARepository/prod/CIGActgS11/position/processing/Priority_2/PositionSide/122654_DESK_CDRG183872PositionSide.bcp.SSrvr']
		walker_process = Process(target=walker, args=(walk, put_queue, args, options))
		walker_process.start()
		
	if 1:
		put = {'update': put_update}[options.put]
		#print put
		#e()
		putter_processes = list(islice(repeatedly(Process, target=putter, args=(put, put_queue, stat_queue, options)), options.processes))
		for putter_process in putter_processes:
			#print putter_process
			putter_process.start()
	walker_process.join()
	if 1:
		statter_process = Process(target=statter, args=(stat_queue, start, options))
		statter_process.start()
	
	for putter_process in putter_processes:
		put_queue.put(None)
	put_queue.close()
	for putter_process in putter_processes:
		putter_process.join()
		
	stat_queue.put(None)
	stat_queue.close()
	statter_process.join()
	put_queue.join_thread()
	stat_queue.join_thread()

if __name__ == '__main__':
	sys.exit(main(sys.argv))
