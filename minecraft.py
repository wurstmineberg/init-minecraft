#!/usr/bin/env python3

"""Minecraft server script for init.d.

Usage:
  minecraft [options] start | stop | backup | status | restart
  minecraft [options] update [snapshot <snapshot-id> | VERSION]
  minecraft [options] command COMMAND...
  minecraft -h | --help
  minecraft --version

Options:
  -h, --help         Print this message and exit.
  --config=<config>  Path to the config file [default: /opt/wurstmineberg/config/init-minecraft.json].
  --version          Print version info and exit.
"""

__version__ = '2.13.6'

import sys

sys.path.append('/opt/py')

from datetime import date
from datetime import datetime
from docopt import docopt
from datetime import time as dtime
import errno
import gzip
import json
import os
import os.path
import re
import requests
import socket
import subprocess
import time
from datetime import timezone

CONFIG_FILE = '/opt/wurstmineberg/config/init-minecraft.json'
if __name__ == '__main__':
    arguments = docopt(__doc__, version='minecraft init script ' + __version__)
    CONFIG_FILE = arguments['--config']

def config(key=None, default_value=None):
    default_config = {
        'java_options': {
            'cpu_count': 1,
            'jar_options': ['nogui'],
            'max_heap': 4096,
            'min_heap': 2048
        },
        'paths': {
            'assets': '/var/www/wurstmineberg.de/assets/serverstatus',
            'backup': '/opt/wurstmineberg/backup',
            'backupweb': '/var/www/wurstmineberg.de/latestbackup.tar.gz',
            'client_versions': '/opt/wurstmineberg/home/.minecraft/versions',
            'commandlog': '/opt/wurstmineberg/log/commands.log',
            'home': '/opt/wurstmineberg',
            'httpdocs': '/var/www/wurstmineberg.de',
            'jar': '/opt/wurstmineberg/server/jar',
            'log': '/opt/wurstmineberg/log',
            'people': '/opt/wurstmineberg/config/people.json',
            'server': '/opt/wurstmineberg/server',
            'service': '/opt/wurstmineberg/server/service_name',
            'socket': '/var/local/wurstmineberg/minecraft_commands.sock'
        },
        'service_name': 'minecraft_server.jar',
        'usc': False,
        'username': 'wurstmineberg',
        'whitelist': {
            'additional': [],
            'ignore_people': False
        },
        'world': 'wurstmineberg'
    }
    try:
        with open(CONFIG_FILE) as config_file:
            j = json.load(config_file)
    except:
        j = default_config
    if key is None:
        return j
    return j.get(key, default_config.get(key)) if default_value is None else j.get(key, default_value)

INVOCATION = ['java', '-Xmx' + str(config('java_options')['max_heap']) + 'M', '-Xms' + str(config('java_options')['min_heap']) + 'M', '-XX:+UseConcMarkSweepGC', '-XX:+CMSIncrementalMode', '-XX:+CMSIncrementalPacing', '-XX:ParallelGCThreads=' + str(config('java_options')['cpu_count']), '-XX:+AggressiveOpts', '-jar', config('paths')['service']] + config('java_options')['jar_options']

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
            return datetime.strptime(base_date + timestamp, '%Y-%m-%d[%H:%M:%S]')
        hour = int(timestamp[1:3])
        minute = int(timestamp[4:6])
        second = int(timestamp[7:9])
        return datetime.combine(base_date, dtime(hour=hour, minute=minute, second=second, tzinfo=timezone.utc))

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
    backup_file = os.path.join(config('paths')['backup'], config('world') + '_' + now + '.tar')
    print('Backing up minecraft world...')
    subprocess.call(['tar', '-C', config('paths')['server'], '-cf', backup_file, config('world')])
    subprocess.call(['rsync', '-av', '--delete', os.path.join(config('paths')['server'], config('world')) + '/', os.path.join(config('paths')['backup'], 'latest')])
    saveon(announce=announce)
    print('Compressing backup...')
    subprocess.call(['gzip', '-f', backup_file])
    print('Symlinking to httpdocs...')
    if os.path.lexists(config('paths')['backupweb']):
        os.unlink(config('paths')['backupweb'])
    os.symlink(backup_file + '.gz', config('paths')['backupweb'])
    print('Done.')

def command(cmd, args=[], block=False, subst=True):
    # raises socket.error if Minecraft is disconnected
    def file_len(file): #FROM http://stackoverflow.com/questions/845058/how-to-get-line-count-cheaply-in-python
        for i, l in enumerate(file):
            pass
        return i + 1
    
    if (not block) and not status():
        return None
    try:
        with open(os.path.join(config('paths')['server'], 'logs', 'latest.log')) as logfile:
            pre_log_len = file_len(logfile)
    except (IOError, OSError):
        pre_log_len = 0
    except:
        pre_log_len = None
    cmd += (' ' + ' '.join(str(arg) for arg in args)) if len(args) else ''
    with socket.socket(socket.AF_UNIX) as s:
        s.connect(config('paths')['socket'])
        s.sendall(cmd.encode('utf-8') + b'\n')
    if pre_log_len is None:
        return None
    time.sleep(0.2) # assumes that the command will run and print to the log file in less than .2 seconds
    return _command_output('tail', ['-n', '+' + str(pre_log_len + 1), os.path.join(config('paths')['server'], 'logs', 'latest.log')])

def last_seen(player):
    for timestamp, _, logline in log(reverse=True):
        match = re.match(re.escape(player) + ' left the game', logline)
        if match and (timestamp is not None):
            return timestamp
    return None

def log(reverse=False):
    if reverse:
        try:
            with open(os.path.join(config('paths')['server'], 'logs', 'latest.log')) as logfile:
                for line in reversed(list(logfile)):
                    match = re.match('(' + regexes.timestamp + ') ' + regexes.prefix + ' (.*)$', line)
                    if match:
                        yield regexes.strptime(date.today(), match.group(1)), match.group(2), match.group(3)
                    else:
                        yield None, None, line.rstrip('\r\n')
        except (IOError, OSError):
            pass
        try:
            for logfilename in sorted(os.listdir(os.path.join(config('paths')['server'], 'logs')), reverse=True):
                if not logfilename.endswith('.log.gz'):
                    continue
                try:
                    with gzip.open(os.path.join(config('paths')['server'], 'logs', logfilename)) as logfile:
                        log_bytes = logfile.read()
                except (IOError, OSError):
                    continue
                for line in reversed(log_bytes.decode('utf-8').splitlines()):
                    match = re.match('(' + regexes.timestamp + ') ' + regexes.prefix + ' (.*)$', line)
                    if match:
                        yield regexes.strptime(logfilename[:10], match.group(1)), match.group(2), match.group(3)
                    else:
                        yield None, None, line
        except (IOError, OSError):
            pass
        try:
            with open(os.path.join(config('paths')['server'], 'server.log')) as logfile:
                for line in reversed(list(logfile)):
                     match = re.match('(' + regexes.old_timestamp + ') ' + regexes.prefix + ' (.*)$', line)
                     if match:
                         yield datetime.strptime(match.group(1) + ' +0000', '%Y-%m-%d %H:%M:%S %z') , match.group(2), match.group(3)
                     else:
                         yield None, None, line.rstrip('\r\n')
        except (IOError, OSError):
            pass
    else:
        try:
            with open(os.path.join(config('paths')['server'], 'server.log')) as logfile:
                for line in logfile:
                     match = re.match('(' + regexes.old_timestamp + ') ' + regexes.prefix + ' (.*)$', line)
                     if match:
                         yield datetime.strptime(match.group(1) + ' +0000', '%Y-%m-%d %H:%M:%S %z') , match.group(2), match.group(3)
                     else:
                         yield None, None, line.rstrip('\r\n')
        except (IOError, OSError):
            pass
        try:
            for logfilename in sorted(os.listdir(os.path.join(config('paths')['server'], 'logs'))):
                if not logfilename.endswith('.log.gz'):
                    continue
                try:
                    with gzip.open(os.path.join(config('paths')['server'], 'logs', logfilename)) as logfile:
                        log_bytes = logfile.read()
                except (IOError, OSError):
                    continue
                for line in log_bytes.decode('utf-8').splitlines():
                    match = re.match('(' + regexes.timestamp + ') ' + regexes.prefix + ' (.*)$', line)
                    if match:
                        yield regexes.strptime(logfilename[:10], match.group(1)), match.group(2), match.group(3)
                    else:
                        yield None, None, line
        except (IOError, OSError):
            pass
        try:
            with open(os.path.join(config('paths')['server'], 'logs', 'latest.log')) as logfile:
                for line in logfile:
                    match = re.match('(' + regexes.timestamp + ') ' + regexes.prefix + ' (.*)$', line)
                    if match:
                        yield regexes.strptime(date.today(), match.group(1)), match.group(2), match.group(3)
                    else:
                        yield None, None, line.rstrip('\r\n')
        except (IOError, OSError):
            pass

def online_players(retry=True):
    found = False
    try:
        list = command('list')
    except socket.error:
        return []
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
        print('Minecraft is running... suspending saves')
        if announce:
            say('Server backup starting. Server going readonly...')
        command('save-off')
        command('save-all')
        subprocess.call(['sync'])
        time.sleep(10)
    else:
        print('Minecraft is not running. Not suspending saves.')

def saveon(announce=True):
    if status():
        print('Minecraft is running... re-enabling saves')
        command('save-on')
        if announce:
            say('Server backup ended. Server going readwrite...')
    else:
        print('Minecraft is not running. Not resuming saves.')

def say(message, prefix=True):
    if prefix:
        command('say', [message])
    else:
        tellraw(message)

def start(*args, **kwargs):
    reply = kwargs.get('reply', print)
    def _start(timeout=0.1):
        with open(os.path.devnull) as devnull:
            javapopen = subprocess.Popen(INVOCATION, stdin=subprocess.PIPE, stdout=devnull, cwd=config('paths')['server'])
        loopvar = True
        with socket.socket(socket.AF_UNIX) as s:
            if os.path.exists(config('paths')['socket']):
                os.remove(config('paths')['socket'])
            s.bind(config('paths')['socket'])
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
        if os.path.exists(config('paths')['socket']):
            os.remove(config('paths')['socket'])
    
    if status():
        reply('Server is already running!')
        return False
    else:
        reply(kwargs.get('start_message', 'starting Minecraft server...'))
        _fork(_start)
        time.sleep(7)
        update_status()
        return status()

def status():
    with open(os.devnull, 'a') as devnull:
        return not subprocess.call(['pgrep', '-u', 'wurstmineberg', '-f', config('service_name')], stdout=devnull)

def stop(*args, **kwargs):
    reply = kwargs.get('reply', print)
    if status():
        reply('SERVER SHUTTING DOWN IN 10 SECONDS. Saving map...')
        say('SERVER SHUTTING DOWN IN 10 SECONDS. Saving map...')
        command('save-all')
        time.sleep(10)
        command('stop')
        time.sleep(7)
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
    subprocess.check_call(['wget', 'https://s3.amazonaws.com/Minecraft.Download/versions/' + version + '/minecraft_server.' + version + '.jar'], cwd=config('paths')['jar'])
    subprocess.check_call(['wget', 'https://s3.amazonaws.com/Minecraft.Download/versions/' + version + '/' + version + '.jar', '-P', os.path.join(config('paths')['client_versions'], version)])
    say('Server will be upgrading to ' + version_text + ' and therefore restart')
    time.sleep(5)
    stop(reply=reply)
    if os.path.lexists(config('paths')['service']):
        os.unlink(config('paths')['service'])
    os.symlink(os.path.join(config('paths')['jar'], 'minecraft_server.' + version + '.jar'), config('paths')['service'])
    if os.path.lexists(os.path.join(config('paths')['home'], 'home', 'client.jar')):
        os.unlink(os.path.join(config('paths')['home'], 'home', 'client.jar'))
    os.symlink(os.path.join(config('paths')['client_versions'], version, version + '.jar'), os.path.join(config('paths')['home'], 'home', 'client.jar'))
    start(reply=reply, start_message='Server updated. Restarting...')
    return version, snapshot, version_text

def update_status():
    d = {
        'list': online_players(),
        'on': status(),
        'version': version()
    }
    with open(os.path.join(config('paths')['assets'], 'status.json'), 'w') as statusjson:
        json.dump(d, statusjson, sort_keys=True, indent=4, separators=(',', ': '))

def update_whitelist(people_file=None):
    if people_file is None:
        people_file = config('paths')['people']
    with open(os.path.join(config('paths')['server'], 'white-list.txt'), 'w') as whitelistfile:
        print('# DO NOT EDIT THIS FILE', file=whitelistfile)
        print('# it is automatically generated from ' + people_file, file=whitelistfile)
        print('# all changes will be lost on the next auto-update', file=whitelistfile)
        print(file=whitelistfile)
        if not config('whitelist').get('ignore_people', False):
            with open(people_file) as people:
                for person in json.load(people):
                    if not person.get('minecraft'):
                        continue
                    if person.get('status', 'later') not in ['founding', 'later', 'postfreeze']:
                        continue
                    print(person['minecraft'], file=whitelistfile)
        additional = config('whitelist').get('additional', [])
        if len(additional) > 0:
            print('# additional nicks generated from ' + CONFIG_FILE + ':')
            for minecraft_nick in additional:
                print(minecraft_nick, file=whitelistfile)
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
    if arguments['start']:
        if start():
            print('[ ok ] minecraft is now running.')
        else:
            print('[FAIL] Error! Could not start minecraft.')
    elif arguments['stop']:
        if stop():
            print('[ ok ] minecraft is stopped.')
        else:
            print('[FAIL] Error! minecraft could not be stopped.')
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
        print('[info] minecraft is ' + ('running.' if status() else 'not running.'))
    elif arguments['command']:
        cmdlog = command(arguments['COMMAND'][0], arguments['COMMAND'][1:])
        for line in cmdlog.splitlines():
            print(str(line))
