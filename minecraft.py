#!/usr/bin/env python3

"""Minecraft server script for init.d.

Usage:
  minecraft start | stop | backup | status | restart
  minecraft update [snapshot <snapshot-id> | VERSION]
  minecraft command COMMAND...
  minecraft -h | --help
  minecraft --version

Options:
  -h, --help  Print this message and exit.
  --version   Print version info and exit.
"""

__version__ = '2.12.5'

import sys

sys.path.append('/opt/py')

from datetime import date
from datetime import datetime
from docopt import docopt
import errno
import gzip
import json
import line64
import os
import os.path
import pty
import re
import requests
import shlex
import socket
import subprocess
import time
from datetime import time as dtime

MCHOME = '/opt/wurstmineberg'
HTTPDOCS = '/var/www/wurstmineberg.de'
LOGDIR = os.path.join(MCHOME, 'log')

ASSETS = os.path.join(HTTPDOCS, 'assets', 'serverstatus')
BACKUPPATH = os.path.join(MCHOME, 'backup')
BACKUPWEB = os.path.join(HTTPDOCS, 'latestbackup.tar.gz')
CMDLOG = os.path.join(LOGDIR, 'commands.log')
CPU_COUNT = 1
MAXHEAP = 4096
MINHEAP = 2048
MCPATH = os.path.join(MCHOME, 'server')
JARDIR = os.path.join(MCPATH, 'jar')
OPTIONS = ['nogui']
SERVICE = 'minecraft_server.jar'
SOCKPATH = '/var/local/wurstmineberg/minecraft_commands.sock'
USERNAME = 'wurstmineberg'
WORLD = 'wurstmineberg'

INVOCATION = ['java', '-Xmx' + str(MAXHEAP) + 'M', '-Xms' + str(MINHEAP) + 'M', '-XX:+UseConcMarkSweepGC', '-XX:+CMSIncrementalMode', '-XX:+CMSIncrementalPacing', '-XX:ParallelGCThreads=' + str(CPU_COUNT), '-XX:+AggressiveOpts', '-jar', SERVICE] + OPTIONS

class MinecraftServerNotRunningError(Exception):
    pass

class regexes:
    old_timestamp = '[0-9]{4}-[0-9]{2}-[0-9]{2} [0-9]{2}:[0-9]{2}:[0-9]{2}'
    player = '[A-Za-z0-9_]{1,16}'
    prefix = '\\[(.+?)\\]:?'
    timestamp = '\\[[0-9]{2}:[0-9]{2}:[0-9]{2}\\]'
    
    @staticmethod
    def strptime(base_date, timestamp):
        # return UTC datetime object from log timestamp
        if isinstance(base_date, str):
            base_date = date.strptime(base_date, '%Y-%m-%d')
        return datetime.combine(base_date, dtime.strptime(timestamp + '+0000', '[%H:%M:%S]%z'))

def _command_output(cmd, args=[]):
    p = subprocess.Popen([cmd] + args, stdout=subprocess.PIPE)
    out, _ = p.communicate()
    return out.decode('utf-8')

def _fork(func):
    #FROM http://stackoverflow.com/a/6011298/667338
    # do the UNIX double-fork magic, see Stevens' "Advanced Programming in the UNIX Environment" for details (ISBN 0201563177)
    try: 
        pid = os.fork() 
        if pid > 0:
            # parent process, return and keep running
            return
    except OSError as e:
        print('fork #1 failed: %d (%s)' % (e.errno, e.strerror), file=sys.stderr)
        sys.exit(1)
    os.setsid()
    # do second fork
    try: 
        pid = os.fork() 
        if pid > 0:
            # exit from second parent
            sys.exit(0) 
    except OSError as e: 
        print('fork #2 failed: %d (%s)' % (e.errno, e.strerror), file=sys.stderr)
        sys.exit(1)
    with open(os.path.devnull) as devnull:
        sys.stdin = devnull
        sys.stdout = devnull
        func() # do stuff
        os._exit(os.EX_OK) # all done

def backup(announce=False):
    saveoff(announce=announce)
    now = datetime.utcnow().strftime('%Y-%m-%d_%Hh%M')
    backup_file = BACKUPPATH + '/' + WORLD + '_' + now + '.tar'
    print('Backing up minecraft world...')
    subprocess.call(['tar', '-C', MCPATH, '-cf', backup_file, WORLD])
    print('Backing up ' + SERVICE)
    subprocess.call(['rsync', '-av', os.path.join(MCPATH, WORLD) + '/', os.path.join(BACKUPPATH, 'latest')])
    saveon(announce=announce)
    print('Compressing backup...')
    subprocess.call(['gzip', '-f', backup_file])
    print('Symlinking to httpdocs...')
    if os.path.lexists(BACKUPWEB):
        os.unlink(BACKUPWEB)
    os.symlink(backup_file + '.gz', BACKUPWEB)
    print('Done.')

def command(cmd, args=[], block=False, subst=True):
    def file_len(file): #FROM http://stackoverflow.com/questions/845058/how-to-get-line-count-cheaply-in-python
        for i, l in enumerate(file):
            pass
        return i + 1
    
    if (not block) and not status():
        return None
    #pre_log_len = len(list(log()))
    with open(os.path.join(MCPATH, 'logs', 'latest.log')) as logfile:
        pre_log_len = file_len(logfile)
        #print('DEBUG] pre-command log length: ' + str(pre_log_len)) #DEBUG
    cmd += (' ' + ' '.join(str(arg) for arg in args)) if len(args) else ''
    with socket.socket(socket.AF_UNIX) as s:
        s.connect(SOCKPATH)
        s.sendall(cmd.encode('utf-8') + b'\n')
    #with open(CMDPIPE, 'w') as cmdpipe:
    #    print(cmd, file=cmdpipe)
    #subprocess.call(['screen', '-p', '0', '-S', 'minecraft', '-X', 'eval', 'stuff "' + cmd + '"\015'], shell=True) # because nothing else works
    time.sleep(0.2) # assumes that the command will run and print to the log file in less than .2 seconds
    #return list(log())[pre_log_len:]
    return _command_output('tail', ['-n', '+' + str(pre_log_len + 1), os.path.join(MCPATH, 'logs', 'latest.log')])

def last_seen(player):
    for timestamp, _, logline in log(reverse=True):
        match = re.match(re.escape(player) + ' left the game', logline)
        if match and (timestamp is not None):
            return timestamp
    return None

def log(reverse=False):
    if reverse:
        with open(os.path.join(MCPATH, 'logs', 'latest.log')) as logfile:
            for line in reversed(list(logfile)):
                match = re.match('(' + regexes.timestamp + ') ' + regexes.prefix + ' (.*)$', line)
                if match:
                    yield regexes.strptime(date.today(), match.group(1)), match.group(2), match.group(3)
                else:
                    yield None, None, line.rstrip('\r\n')
        for logfilename in sorted(os.listdir(os.path.join(MCPATH, 'logs')), reverse=True):
            if not logfilename.endswith('.log.gz'):
                continue
            with gzip.open(os.path.join(MCPATH, 'logs', logfilename)) as logfile:
                log_bytes = logfile.read()
            for line in reversed(log_bytes.decode('utf-8').splitlines()):
                match = re.match('(' + regexes.timestamp + ') ' + regexes.prefix + ' (.*)$', line)
                if match:
                    yield regexes.strptime(logfilename[:10], match.group(1)), match.group(2), match.group(3)
                else:
                    yield None, None, line
        with open(os.path.join(MCPATH, 'server.log')) as logfile:
            for line in reversed(list(logfile)):
                 match = re.match('(' + regexes.old_timestamp + ') ' + regexes.prefix + ' (.*)$', line)
                 if match:
                     yield datetime.strptime(match.group(1) + ' +0000', '%Y-%m-%d %H:%M:%S %z') , match.group(2), match.group(3)
                 else:
                     yield None, None, line.rstrip('\r\n')
    else:
        with open(os.path.join(MCPATH, 'server.log')) as logfile:
            for line in logfile:
                 match = re.match('(' + regexes.old_timestamp + ') ' + regexes.prefix + ' (.*)$', line)
                 if match:
                     yield datetime.strptime(match.group(1) + ' +0000', '%Y-%m-%d %H:%M:%S %z') , match.group(2), match.group(3)
                 else:
                     yield None, None, line.rstrip('\r\n')
        for logfilename in sorted(os.listdir(os.path.join(MCPATH, 'logs'))):
            if not logfilename.endswith('.log.gz'):
                continue
            with gzip.open(os.path.join(MCPATH, 'logs', logfilename)) as logfile:
                log_bytes = logfile.read()
            for line in log_bytes.decode('utf-8').splitlines():
                match = re.match('(' + regexes.timestamp + ') ' + regexes.prefix + ' (.*)$', line)
                if match:
                    yield regexes.strptime(logfilename[:10], match.group(1)), match.group(2), match.group(3)
                else:
                    yield None, None, line
        with open(os.path.join(MCPATH, 'logs', 'latest.log')) as logfile:
            for line in logfile:
                match = re.match('(' + regexes.timestamp + ') ' + regexes.prefix + ' (.*)$', line)
                if match:
                    yield regexes.strptime(date.today(), match.group(1)), match.group(2), match.group(3)
                else:
                    yield None, None, line.rstrip('\r\n')

def online_players(retry=True):
    found = False
    list = command('list')
    if list is None:
        if retry:
            return online_players(retry=False)
        return []
    for line in list.splitlines():
        if found:
            match = re.match(regexes.timestamp + ' \\[Server thread/INFO\\]: (' + regexes.player + '(, ' + regexes.player + ')*)?', line)
            if match:
                return [] if match.group(1) is None else match.group(1).split(', ')
        found = bool(re.match(regexes.timestamp + ' \\[Server thread/INFO\\]: There are [0-9]+/[0-9]+ players online:' , line))
    # no player list in return
    if retry:
        return online_players(retry=False)
    return []

def restart(*args, **kwargs):
    reply = kwargs.get('reply', print)
    stop(*args, **kwargs)
    for _ in range(6):
        if status():
            time.sleep(5)
            continue
        else:
            break
    else:
        reply('The server could not be stopped! D:')
        return False
    kwargs['start_message'] = 'Server stopped. Restarting...'
    return start(*args, **kwargs)

def saveoff(announce=True):
    if status():
        print(SERVICE + ' is running... suspending saves')
        if announce:
            say('Server backup starting. Server going readonly...')
        command('save-off')
        command('save-all')
        subprocess.call(['sync'])
        time.sleep(10)
    else:
        print(SERVICE + ' is not running. Not suspending saves.')

def saveon(announce=True):
    if status():
        print(SERVICE + ' is running... re-enabling saves')
        command('save-on')
        if announce:
            say('Server backup ended. Server going readwrite...')
    else:
        print(SERVICE + ' is not running. Not resuming saves.')

def say(message, prefix=True):
    if prefix:
        command('say', [message])
    else:
        tellraw(message)

def start(*args, **kwargs):
    reply = kwargs.get('reply', print)
    def _start(timeout=0.1):
        with open(os.path.devnull) as devnull:
            javapopen = subprocess.Popen(INVOCATION, stdin=subprocess.PIPE, stdout=devnull, cwd=MCPATH)
        loopvar = True
        with socket.socket(socket.AF_UNIX) as s:
            if os.path.exists(SOCKPATH):
                os.remove(SOCKPATH)
            s.bind(SOCKPATH)
            while loopvar:
                str_buffer = ''
                s.listen(1)
                c, _ = s.accept()
                while loopvar:
                    data = c.recv(1024)
                    if not data:
                        break
                    lines = (str_buffer + data.decode('utf-8')).split('\n')
                    for line in lines[:-1]:
                        if line == 'stop':
                            loopvar = False
                            break
                        javapopen.stdin.write(line.encode('utf-8') + b'\n')
                    str_buffer = lines[-1]
                c.close()
                if javapopen.poll() is not None:
                    return
        javapopen.communicate(input=b'stop\n')
        if os.path.exists(SOCKPATH):
            os.remove(SOCKPATH)
    
    if status():
        reply('Server is already running!')
        return False
    else:
        reply(kwargs.get('start_message', 'starting Minecraft server...'))
        _fork(_start)
        #subprocess.Popen('screen -dmS minecraft ' + INVOCATION, cwd=MCPATH, shell=True)
        time.sleep(7)
        update_status()
        return status()

def status():
    with open(os.devnull, 'a') as devnull:
        return not subprocess.call(['pgrep', '-u', 'wurstmineberg', '-f', SERVICE], stdout=devnull)

def stop(*args, **kwargs):
    reply = kwargs.get('reply', print)
    if status():
        reply('SERVER SHUTTING DOWN IN 10 SECONDS. Saving map...')
        command('save-all')
        time.sleep(10)
        command('stop')
        time.sleep(7)
        #with open(CMDPIPE, 'w') as cmdpipe:
        #    print('-break', file=cmdpipe)
    else:
        reply('Minecraft server was not running.')
    update_status()
    return not status()

def tellraw(message_dict, player='@a'):
    if isinstance(message_dict, str):
        message_dict = {'text': message_dict}
    elif isinstance(message_dict, list):
        message_dict = {'text': '', 'extra': message_dict}
    command('tellraw', [player, json.dumps(message_dict)])

def update(version=None, snapshot=False, reply=print):
    versions_json = requests.get('https://s3.amazonaws.com/Minecraft.Download/versions/versions.json').json()
    if version is None: # try to dynamically get the latest version number from assets
        version = versions_json['latest']['snapshot' if snapshot else 'release']
    elif snapshot:
        version = datetime.utcnow().strftime("%yw%V") + version
    for version_dict in versions_json['versions']:
        if version_dict.get('id') == version:
            snapshot = version_dict.get('type') == 'snapshot'
            break
    else:
        reply('Minecraft version not found in assets, will try downloading anyway')
        version_dict = None
    version_text = 'Minecraft ' + ('snapshot ' if snapshot else 'version ') + version
    reply('Downloading ' + version_text)
    subprocess.check_call(['wget', 'https://s3.amazonaws.com/Minecraft.Download/versions/' + version + '/minecraft_server.' + version + '.jar'], cwd=JARDIR)
    subprocess.check_call(['wget', 'https://s3.amazonaws.com/Minecraft.Download/versions/' + version + '/' + version + '.jar', '-P', os.path.join(MCHOME, 'home', '.minecraft', 'versions', version)])
    say('Server will be upgrading to ' + version_text + ' and therefore restart')
    time.sleep(5)
    stop(reply=reply)
    if os.path.lexists(os.path.join(MCPATH, SERVICE)):
        os.unlink(os.path.join(MCPATH, SERVICE))
    os.symlink(os.path.join(JARDIR, 'minecraft_server.' + version + '.jar'), os.path.join(MCPATH, SERVICE))
    if os.path.lexists(os.path.join(MCHOME, 'home', 'client.jar')):
        os.unlink(os.path.join(MCHOME, 'home', 'client.jar'))
    os.symlink(os.path.join(MCHOME, 'home', '.minecraft', 'versions', version, version + '.jar'), os.path.join(MCHOME, 'home', 'client.jar'))
    start(reply=reply, start_message='Server updated. Restarting...')
    return version, snapshot, version_text

def update_status():
    d = {
        'list': online_players(),
        'on': status(),
        'version': version()
    }
    with open(ASSETS + '/status.json', 'w') as statusjson:
        json.dump(d, statusjson, sort_keys=True, indent=4, separators=(',', ': '))

def update_whitelist(people_file='/opt/wurstmineberg/config/people.json'):
    with open(MCPATH + '/white-list.txt', 'w') as whitelistfile:
        print('# DO NOT EDIT THIS FILE', file=whitelistfile)
        print('# it is automatically generated from /opt/wurstmineberg/config/people.json', file=whitelistfile)
        print('# all changes will be lost on the next auto-update', file=whitelistfile)
        print(file=whitelistfile)
        with open(people_file) as people:
            for person in json.load(people):
                if not person.get('minecraft'):
                    continue
                if person.get('status', 'later') not in ['founding', 'later', 'postfreeze']:
                    continue
                print(person['minecraft'], file=whitelistfile)
    command('whitelist', ['reload'])

def version():
    for _, _, line in log(reverse=True):
        match = re.match('Starting minecraft server version (.*)', line)
        if match:
            return match.group(1)

def whitelist(people_file='/opt/wurstmineberg/config/people.json'):
    with open(people_file) as people:
       return (person for person in json.load(people) if person.get('status', 'later') in ['founding', 'later', 'postfreeze'])

def whitelist_add(id, minecraft_nick=None, people_file='/opt/wurstmineberg/config/people.json', person_status='postfreeze'):
    with open(people_file) as f:
        people = json.load(f)
    for person in people:
        if person['id'] == id:
            if person['status'] == 'invited':
                person['join_date'] = datetime.utcnow().strftime('%Y-%m-%d')
                if minecraft_nick is not None:
                    person['minecraft'] = minecraft_nick
                person['status'] = person_status
                break
            else:
                raise ValueError('A person with this id already exists')
    else:
        people.append({
            'id': id,
            'join_date': datetime.utcnow().strftime('%Y-%m-%d'),
            'minecraft': minecraft_nick,
            'status': person_status
        })
    with open(people_file, 'w') as f:
        json.dump(people, f, sort_keys=True, indent=4, separators=(',', ': '))
    update_whitelist(people_file=people_file)

if __name__ == '__main__':
    arguments = docopt(__doc__, version='minecraft init script ' + __version__)
    if arguments['start']:
        if start():
            print(SERVICE + ' is now running.')
        else:
            print('Error! Could not start ' + SERVICE + '!')
    elif arguments['stop']:
        if stop():
            print(SERVICE + ' is stopped.')
        else:
            print('Error! ' + SERVICE + ' could not be stopped.')
    elif arguments['restart']:
        restart()
    elif arguments['update']:
        if arguments['snapshot']:
            update(arguments['<snapshot-id>'], snapshot=True)
        elif arguments['VERSION']:
            update(arguments['<snapshot-id>'])
        else:
            update(snapshot=True)
    elif arguments['backup']:
        backup()
    elif arguments['status']:
        print('minecraft is ' + ('running.' if status() else 'not running.'))
    elif arguments['command']:
        cmdlog = command(arguments['COMMAND'][0], arguments['COMMAND'][1:])
        for line in cmdlog.splitlines():
            print(str(line))
