#!/usr/bin/python

# Attempt at decoding higher level meaning from the IQ2020 I2C bus
# Address 0x20: Front control panel
# 0x21: Unknown, but present in my tub (rear control panel?)
# 0x22: Lights
# 0x18: Unknown and not present in my tub
# 0x3E: Unknown and not present in my tub
# 0x36: Unknown and not present in my tub
# 0x44: Unknown and not present in my tub


import csv
import re
import os.path
import subprocess
from optparse import OptionParser
import logging
from collections import namedtuple
from pysqlite2 import dbapi2 as sqlite3
from collections import defaultdict
import inspect
import pprint
import colorama
from colorama import Fore, Back, Style

colorama.init()


ByteRow = namedtuple('ByteRow', ['time_s', 'packet_id', 'address', 'data', 'rw', 'ack'])

# time_s: start time
# packet_id: of first packet
# address: two hex digits
# rw: R|W|WR
# lendata: total number of bytes
# dataid: unique id for this data string
# data: hex digts "XX XX"
Packet = namedtuple('Packet', ['time_s', 'packet_id', 'address', 'rw', 'lendata', 'dataid', 'data', 'datasum', 'ack'])
packet_new_argnames = inspect.getargspec(Packet.__new__).args[1:]

class Message(object):
  def __init__(self, packets):
    self.packets = packets
    self.comment = ""
    # 0 = something not at all understood, 1 = default, 2 = timing way off, 3 = unpredictable state changed, 4 = timing slightly off, 5 = totatlly expected
    self.known = 1

  # unless another property is defined below accessing m.<attribute> is the same as getting m.packets[0].<attribute>
  def __getattr__(self, key):
    return getattr(self.packets[0], key)

  def __setattr__(self, key, value):
    # Known is a magic attribute. when it is 1, the default, it takes the new value. otherwise assignment can only decrease it
    if key == 'known':
      cur_known = self.__dict__.get("known", 1)
      if cur_known != 1:
        value = min(cur_known, value)
    self.__dict__[key] = value

  @property
  def data(self):
    return ' > '.join(map(lambda p: p.data, self.packets))

  @property
  def rw(self):
    return ''.join(map(lambda p: p.rw, self.packets))

  @property
  def ack(self):
    return ''.join(map(lambda p: p.ack, self.packets))

  # __len__ and __getitem__ let Message be treated as a sequence, similar to Packet
  def __len__(self):
    return len(packet_new_argnames)

  def __getitem__(self, key):
    # key should be an int
    argname = packet_new_argnames[key]
    return getattr(self, argname)

  def __repr__(self):
    return str(self.__dict__)


def NewPacket(*args, **kwargs):
  argsout = []
  for i in range(len(packet_new_argnames)):
    argname = packet_new_argnames[i]
    if argname in kwargs:
      v = kwargs[argname]
    elif i < len(args):
      v = args[i]
    else:
      v = packet_arg_default[argname]
    if argname == "known":
      v = int(v)
    argsout.append(v)
  return Packet(*argsout)

# Map from data string as "0x00 0x01" etc to uniq id
data_ids = {}


def PacketSummary(packet_rows, data_ids):
  times, packet_ids, addresses, data, rw, acks = zip(*packet_rows)
  # byte zero of packet
  b0 = packet_rows[0]
  if len(set(packet_ids)) != 1:
    raise Exception(packet_rows)
  if len(set(addresses)) != 1:
    raise Exception(packet_rows)
  data_string = " ".join(data)
  if data_string not in data_ids:
    data_ids[data_string] = len(data_ids) + 1
  data_sum = "%X" % sum(map(lambda d: int(d, 16), data[0:-1]))

  if len(set(rw)) != 1:
    raise Exception(packet_rows)
  ack_string = "".join(map(lambda a: a[0], acks))
  if b0.rw == "R":
    if ack_string == "".join(['A'] * (len(packet_rows) - 1) + ['N']):
      ack_string = ""
  elif b0.rw == "W":
    if ack_string == "".join(['A'] * len(packet_rows)):
      ack_string = ""
  else:
    raise Exception("Unexpected rw %s" % (b0.rw,))
  return NewPacket(b0.time_s, b0.packet_id, b0.address, b0.rw, len(data), data_ids[data_string], data_string, data_sum, ack_string)



def CompareData(string_a, string_b):
  if not (string_a and string_b):
    return "<can't compare>"

  if len(string_a) != len(string_b):
    return ""

  rv = ""
  for a, b in zip(string_a, string_b):
    if a == " " and b == " ":
      rv += " "
    else:
      rv += "%X" % (int(a, 16) ^ int(b, 16))
  return rv


def LoadFile(path):
  csvreader = csv.reader(open(path))
  # Time [s]  Packet ID Address Data  Read/Write  ACK/NAK
  csvreader.next()

  packets = []
  cur_packet_id = None
  cur_packet_rows = []
  for time_s, packet_id, address, data, rw, ack in csvreader:
    # Remove leading 0x from address and data. Truncate rw and ack to R/W, A/N
    row = ByteRow(float(time_s), packet_id, address[2:4], data[2:4], rw[0], ack[0])
    if row.packet_id != cur_packet_id:
      if cur_packet_rows:
        packets.append(PacketSummary(cur_packet_rows, data_ids))
      cur_packet_rows = []
      cur_packet_id = row.packet_id
    cur_packet_rows.append(row)
  if cur_packet_rows:
    packets.append(PacketSummary(cur_packet_rows, data_ids))

  return packets

def GroupPackets(packets):
  #cur_addr = None
  #pattern_counts = defaultdict(lambda: 0)
  #cur_pattern = None
  #for p in packets:
  #  if p.address != cur_addr:
  #    pattern_counts[cur_pattern] += 1
  #    cur_addr = p.address
  #    cur_pattern = ''
  #  cur_pattern += p.rw
  #for c, p in sorted(map(lambda i: (i[1], i[0]), pattern_counts.items())):
  #  print "%5d: %s" % (c, p)

  pp = None # Previous packet
  out = []
  for p in packets:
    if pp and pp.address == p.address and pp.rw == 'W' and p.rw == 'R':
      out[-1] = Message((pp, p))
      pp = None
    else:
      out.append(Message((p,)))
      pp = p
  return out

def EtaToKnown(t, eta):
  if eta[0] <= t <= eta[1]:
    return 5
  elif (eta[0] - 0.001) <= t <= (eta[1] + 0.1):
    return 4
  else:
    return 2

def Eta(time_s, expected_delta_ms, max_delta_ms=None):
  """Return something that can be passed to EtaToKnown. time_s is float absolute time."""
  if max_delta_ms == None:
    return (time_s + 0.8 * expected_delta_ms / 1000, time_s + 1.2 * expected_delta_ms / 1000)
  else:
    return (time_s + 0.8 * expected_delta_ms / 1000, time_s + max_delta_ms / 1000.0)

addr21_first_data = '01 48 05 > 5A 7F 00 00 C0 17 F2'
addr22_values = ['5A 00 00 00 5A', '5A 01 00 00 5B', '5A 02 00 00 58', '5A 03 00 00 59', '5A 04 00 00 5E', '5A 05 00 00 5F']

class State:
  def __init__(self):
    self.addr21_expected_data = addr21_first_data
    self.addr21_eta = (0, 100)
    self.addr21_response = '5A 7F 00 00 C0 17 F2'
    self.addr22_state = '5A 00 00 00 5A'
    self.addr22_eta = (0, 100)
    self.addr20_state = {'01 6B 02': None, '01 39 06': None, '01 62 09': None, '01 3A 0A': None, '02 01 03': None}
    self.addr20_eta = {'02 00 01 01 42': (0, 100), '01 6B 02': (0, 100), '01 39 06': (0, 100), '01 62 09': (0, 100), '02 01 03': (0, 100)}
    self.addr20_prevdata = {}
    self.addr20_prevprevdata = {}
    self.addr20_prevmessage = {}
    self.addr_map_eta = {
      '18': (0, 100),
      '3E': (0, 100),
      '36': (0, 100),
      '44': (0, 100) }
    self.addr_map_nack_byte = {
      '18': '30',
      '3E': '7C',
      '36': '6C',
      '44': '88'}


  def RateMessage(self, message):
    if message.address in self.addr_map_eta:
      message.known = EtaToKnown(message.time_s, self.addr_map_eta[message.address])
      # 80ms is typical. address 36 is often much slower when stuff is happening
      self.addr_map_eta[message.address] = (message.time_s + 0.003, message.time_s + 0.5)
      if message.data != self.addr_map_nack_byte[message.address]:
        message.known = 0
        message.comment = "Expecting %s" % self.addr_map_nack_byte[message.address]
      if message.ack != 'N' or message.rw != 'W':
        message.known = 0
        message.comment = "expecting NACK Write"
    elif message.address == '21':
      if message.rw == 'WR':
        message.known = EtaToKnown(message.time_s, self.addr21_eta)
        if message.data != self.addr21_expected_data:
          message.known = 3

        # Setup prediction for next message
        if message.packets[0].lendata == 6:
          self.addr21_eta = (message.time_s + 0.045, message.time_s + 0.055)
          self.addr21_expected_data = '01 48 05 > ' + self.addr21_response
        elif message.packets[0].data == '01 48 05':
          if self.addr21_response != message.packets[1].data:
            self.addr21_response = message.packets[1].data
            message.comment = "Data changed to %s" % self.addr21_response
          self.addr21_eta = Eta(message.time_s, 30)
          self.addr21_expected_data = '01 46 02 07 08 08 > 5A 5A'
      else:
        message.known = 0
        self.addr21_expected_data = addr21_first_data
        self.addr21_eta = Eta(message.time_s, 80)
    elif message.address == '22':
      if message.rw == 'WR':
        message.known = EtaToKnown(message.time_s, self.addr22_eta)
        if message.packets[0].data == '00 02 03':
          if message.packets[1].data == self.addr22_state:
            message.known = 5
          else:
            message.knowndata = 0
            message.comment = "Data changed bits %s to %s" % (CompareData(self.addr22_state, message.packets[1].data), self.addr22_state)
            self.addr22_state = message.packets[1].data
          self.addr22_eta = Eta(message.time_s, 80)
        elif message.data == '00 00 02 03 01 44 > 5A 5A':
          message.known = 3
          message.comment = "Increase sent to light"
          if self.addr22_state == addr22_values[0]:
            self.addr22_state = addr22_values[-1]
          self.addr22_eta = Eta(message.time_s, 3)
        elif message.data == '00 00 02 02 01 45 > 5A 5A':
          message.known = 3
          if self.addr22_state in addr22_values:
            self.addr22_state = addr22_values[addr22_values.index(self.addr22_state) - 1]
          message.comment = "Decrease sent to light"
          self.addr22_eta = Eta(message.time_s, 3)
        else:
          message.known = 0
      else:
        message.known = 0
    elif message.address == '20' and message.rw == 'WR':
      if message.packets[0].data in ('01 6B 02', '01 39 06', '01 62 09'):
        subaddr = message.packets[0].data
        data = message.packets[1].data
        message.known = EtaToKnown(message.time_s, self.addr20_eta[subaddr])
        if self.addr20_prevdata.get(subaddr) == data:
          message.known = 5
        else:
          if self.addr20_prevprevdata.get(subaddr) == data:
            message.known = 5
            self.addr20_prevmessage[subaddr].comment += " but only once"
          else:
            message.known = 3
            message.comment = "Changed bits %s to %s" % (CompareData(self.addr20_prevdata.get(subaddr), data), data)
        self.addr20_prevprevdata[subaddr] = self.addr20_prevdata.get(subaddr)
        self.addr20_prevdata[subaddr] = data
        self.addr20_prevmessage[subaddr] = message
        self.addr20_eta[subaddr] = Eta(message.time_s, 80)
      elif message.packets[0].data.startswith('02 01 03'):
        message.known = EtaToKnown(message.time_s, self.addr20_eta['02 01 03'])
        self.addr20_eta['02 01 03'] = Eta(message.time_s, 80)
        if self.addr20_state['02 01 03'] == message.data:
          message.known = 5
        else:
          self.addr20_state['02 01 03'] = message.data
          message.known = 3
          message.comment = "New written data %s" % message.data
      elif message.data == '02 00 01 01 42 > 5A 5A':
        message.known = EtaToKnown(message.time_s, self.addr20_eta['02 00 01 01 42'])
        self.addr20_eta['02 00 01 01 42'] = Eta(message.time_s, 80)
      elif message.packets[0].data.startswith('01 3A 0A'):
        if self.addr20_state['01 3A 0A'] == message.data:
          message.known = 5
        else:
          self.addr20_state['01 3A 0A'] = message.data
          message.known = 3
          message.comment = "New written data %s" % message.data
      else:
        message.known = 0
    return message

def SavePackets(path, packets, pathout, db):
  if db:
    cur = db.cursor()
  csvwriter = csv.writer(open(pathout, 'w'))
  csvwriter.writerow(['Time [s]',  'Packet ID', 'Address', 'Read/Write', 'Len', 'DataId', 'Data', 'Data sum', 'ACK/NAK'])
  for pack in packets:
    csvwriter.writerow(pack)
    if db:
      cur.execute("INSERT INTO packet " +
                  "(id, source_file, time, packet_id, address, rw, data, datasum, ack) " +
                  "VALUES (NULL, ?, ?, ?, ?, ?, ?, ?, ?)",
                  (path, pack.time_s, pack.packet_id, pack.address, pack.rw, pack.data, pack.datasum, pack.ack))
  if db:
    db.commit()

def DumpSummary(packets, output_timesummary, output_json):
  message_data_counts = defaultdict(lambda: 0)
  for p in packets:
    message_data_counts[p.data] += 1

  # TODO: replace with itertools.groupby
  message_times_by_type = defaultdict(list) # Not sure why this doesn't raise TypeError: descriptor 'append' requires a 'list' object but received a 'int'
  messages = []
  for p in packets:
    if message_data_counts[p.data] < 10:
      data = p.packets[0].data
    else:
      data = p.data
    mtype = (p.address, p.rw, p.ack, data, p.known)
    message_times_by_type[mtype].append(p.time_s)
    messages.append((p.time_s, (mtype)))

  if output_timesummary:
    messages.sort()  # Sort by time
    cur_mtype = None
    cur_start_time_s = None
    cur_end_time_s = None
    cur_count = None
    for time_s, mtype in messages:
      if mtype != cur_mtype:
        if cur_mtype is not None:
          print "From %0.3f to %0.3f %4d messages of %s: %s %s %d" % (cur_start_time_s, cur_end_time_s, cur_count, cur_mtype[0], cur_mtype[1], cur_mtype[3], cur_mtype[4])
        cur_mtype = mtype
        cur_start_time_s = time_s
        cur_count = 0
      cur_end_time_s = time_s
      cur_count += 1
    print "From %0.3f to %0.3f %4d messages of %s: %s %s %d" % (cur_start_time_s, cur_end_time_s, cur_count, cur_mtype[0], cur_mtype[1], cur_mtype[3], cur_mtype[4])

  if output_json:
    message_types = message_times_by_type.keys()
    # Sort by length of data, then by content of data
    message_types.sort(key=lambda t: (len(t[3]), t[3]))
    for i, k in enumerate(message_types):
      message_times_by_type[k].sort()
      message_type = "%s %s %s" % (k[0], k[1], k[3])
      for t in message_times_by_type[k]:
        print "{x:%0.6f, y:%d, value:\"%s\"}," % (float(t), i, message_type)

colors = [Back.RED, Fore.RED, Fore.YELLOW, Fore.BLUE, Fore.WHITE, Fore.GREEN]

def PrintPackets(packets, hide_known):
  prev_time_s = 0
  for p in packets:
    if hide_known and p.known >= 5:
      continue
    style = colors[p.known - 1]
    delta_time_s = p.time_s - prev_time_s
    print (style + "%0.3f +%0.3f %s %2s %s %s %s %s" + Style.RESET_ALL) % (p.time_s, delta_time_s, p.address, p.rw, p.ack, p.data, "*" * p.known, p.comment)
    prev_time_s = p.time_s

def main():
  parser = OptionParser(usage='%prog [options]')
  parser.add_option('-d', '--database', dest='database', help='path of sqlite db', metavar='FILE')
  parser.add_option('-t', '--output_timesummary', dest='output_timesummary', action='store_true',
      help='Output some kind of timeline summary thing', default=False)
  parser.add_option('-j', '--output_json', dest='output_json', action='store_true',
      help='Output some kind of json thing', default=False)
  parser.add_option('-g', '--group_packets', dest='group_packets', action='store_true',
      help='Pair up write-read pairs', default=False)
  parser.add_option('-s', '--stateful_filter', dest='stateful_filter', action='store_true',
      help='Send packets through a filter that attempts to track state', default=False)
  parser.add_option('-a', '--address_re', dest='address_re',
      help='regular expression for an address. if set address must match this', metavar='RE')
  parser.add_option('-r', '--data_re', dest='data_re',
      help='regular expression for data. if set data must match this', metavar='RE')
  parser.add_option('-p', '--print_packets', dest='print_packets', action='store_true',
      help='Print packets with color and timing', default=False)
  parser.add_option('--dump_packets', dest='dump_packets', action='store_true',
      help='Print packets without filtering, color or timing', default=False)
  parser.add_option('--hide_known', dest='hide_known', action='store_true',
      help='If set messages that are fully known, 5, are not printed')
  parser.add_option('--noout', dest='noout', action='store_true',
      help='If set output is not written from X.csv to Xout.csv')

  (options,args) = parser.parse_args()

  db = None
  if options.database:
    if not os.path.isfile(options.database):
      subprocess.call("sqlite3 '%s' < initdb.sql" % options.database, shell=True)
    db = sqlite3.connect(options.database)

  for path in args:
    outpath = path
    outpath = re.sub(r'.csv$', '', outpath) + "out.csv"
    packets = LoadFile(path)
    if options.group_packets:
      packets = GroupPackets(packets)
    if options.stateful_filter:
      sf = State()
      packets = map(lambda p: sf.RateMessage(p), packets)
    if not options.noout:
      SavePackets(path, packets, outpath, db)
    if options.address_re:
      packets = filter(lambda p: re.match(options.address_re, p.address) is not None, packets)
    if options.data_re:
      packets = filter(lambda p: re.match(options.data_re, p.data) is not None, packets)
    if options.output_timesummary or options.output_json:
      DumpSummary(packets, options.output_timesummary, options.output_json)
    if options.dump_packets:
      for p in packets:
        if len(p.data) > 3:
          print p.data
    if options.print_packets:
      PrintPackets(packets, options.hide_known)


if __name__ == '__main__':
  try:
    import traceplus
  except ImportError:
    main()
  else:
    traceplus.RunWithExpandedTrace(main)
