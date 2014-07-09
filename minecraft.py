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

__version__ = '2.18.0'

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
from datetime import timedelta
from datetime import timezone
import urllib.parse

CONFIG_FILE = '/opt/wurstmineberg/config/init-minecraft.json'
if __name__ == '__main__':
    arguments = docopt(__doc__, version='Minecraft init script ' + __version__)
    CONFIG_FILE = arguments['--config']

def config(key=None, default_value=None):
    """Get the item with the given key from the config file.

    Optional arguments:
    key -- the key from the config dict to return. If not present or None, the entire config will be returned.
    default_value -- If the specified key is not present in the config file, this argument is returned. If not present of None, a pre-set default is returned instead.
    """
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
            'logConfig': 'log4j2.xml',
            'people': '/opt/wurstmineberg/config/people.json',
            'server': '/opt/wurstmineberg/server',
            'service': '/opt/wurstmineberg/server/minecraft_server.jar',
            'socket': '/var/local/wurstmineberg/minecraft_commands.sock'
        },
        'service_name': 'minecraft_server.jar',
        'usc': False,
        'username': 'wurstmineberg',
        'utc_offset': 0,
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

class MinecraftServerNotRunningError(Exception):
    pass

class regexes:
    full_timestamp = '[0-9]{4}-[0-9]{2}-[0-9]{2} [0-9]{2}:[0-9]{2}:[0-9]{2}'
    player = '[A-Za-z0-9_]{1,16}'
    prefix = '\\[(.+?)\\]:?'
    timestamp = '\\[[0-9]{2}:[0-9]{2}:[0-9]{2}\\]'
    
    @staticmethod
    def strptime(base_date, timestamp, tzinfo=timezone.utc):
        # return aware datetime object from log timestamp
        if isinstance(base_date, str):
            offset = tzinfo.utcoffset(datetime.now())
            if offset < timedelta():
                prefix = '-'
                offset *= -1
            else:
                prefix = '+'
            timezone_string = prefix + str(offset // timedelta(hours=1)).rjust(2, '0') + str(offset // timedelta(minutes=1) % 60).rjust(2, '0')
            return datetime.strptime(base_date + timestamp + timezone_string, '%Y-%m-%d[%H:%M:%S]%z')
        hour = int(timestamp[1:3])
        minute = int(timestamp[4:6])
        second = int(timestamp[7:9])
        return datetime.combine(base_date, dtime(hour=hour, minute=minute, second=second, tzinfo=tzinfo))

def _command_output(cmd, args=[]):
    p = subprocess.Popen([cmd] + args, stdout=subprocess.PIPE)
    out, _ = p.communicate()
    return out.decode('utf-8')

def _download(url, local_filename=None): #FROM http://stackoverflow.com/a/16696317/667338
    if local_filename is None:
        local_filename = url.split('#')[0].split('?')[0].split('/')[-1]
        if local_filename == '':
            raise ValueError('no local filename specified')
    r = requests.get(url, stream=True)
    with open(local_filename, 'wb') as f:
        for chunk in r.iter_content(chunk_size=1024):
            if chunk: # filter out keep-alive new chunks
                f.write(chunk)
                f.flush()

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

def backup(announce=False, reply=print, path=None):
    """Back up the Minecraft world.
    
    Optional arguments:
    announce -- Whether to announce in-game that saves are being disabled/reenabled.
    reply -- This function is called with human-readable progress updates. Defaults to the built-in print function.
    path -- Where the backup will be saved. The file extension .tar.gz will be appended automatically. Defaults to a file with the world name and a timestamp in the backups directory.
    """
    save_off(announce=announce, reply=reply)
    if path is None:
        now = datetime.utcnow().strftime('%Y-%m-%d_%Hh%M')
        path = os.path.join(config('paths')['backup'], config('world') + '_' + now)
    backup_file = path + '.tar'
    reply('Backing up minecraft world...')
    subprocess.call(['tar', '-C', config('paths')['server'], '-cf', backup_file, config('world')])
    subprocess.call(['rsync', '-av', '--delete', os.path.join(config('paths')['server'], config('world')) + '/', os.path.join(config('paths')['backup'], 'latest')])
    save_on(announce=announce, reply=reply)
    reply('Compressing backup...')
    subprocess.call(['gzip', '-f', backup_file])
    backup_file += '.gz'
    reply('Symlinking to httpdocs...')
    if os.path.lexists(config('paths')['backupweb']):
        os.unlink(config('paths')['backupweb'])
    os.symlink(backup_file, config('paths')['backupweb'])
    reply('Done.')

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

def enable_world(world_name, **kwargs):
    """Switch to a different server.properties file.
    
    Required arguments:
    world_name -- The name of the world to be enabled. There must be a file called server.properties.<world_name> in the server directory.
    
    Keyword-only arguments:
    reply -- This function is called with human-readable progress updates. Defaults to the built-in print function.
    start_message -- This string will be passed to the start function when restarting with the new world.
    """
    reply = kwargs.get('reply', print)
    was_running = status()
    if was_running:
        stop(**kwargs)
        for _ in range(6):
            if status():
                time.sleep(5)
                continue
            else:
                break
        else:
            reply('Could not stop the server! World will not be switched.')
            return False
        reply('Server stopped. Switching world...')
    os.unlink(os.path.join(config('paths')['server'], 'server.properties'))
    os.symlink(os.path.join(config('paths')['server'], 'server.properties.' + world_name), os.path.join(config('paths')['server'], 'server.properties'))
    if was_running:
        kwargs['start_message'] = kwargs.get('start_message', 'World switched. Restarting...')
        return start(**kwargs)
    else:
        return True

def last_seen(player, logins_log=None):
    if logins_log is None:
        for timestamp, _, logline in log(reverse=True):
            match = re.match(re.escape(player) + ' left the game', logline)
            if match and (timestamp is not None):
                return timestamp
    else:
        if hasattr(player, 'id'): # support for wurstminebot.nicksub.Person objects
            player = player.id
        with open(logins_log) as logins:
            for line in reversed(list(logins)):
                match = re.match('(' + regexes.full_timestamp + ') (' + regexes.player + ')', line)
                if match and match.group(2) == player:
                    return datetime.strptime(match.group(1) + ' +0000', '%Y-%m-%d %H:%M:%S %z')

def log(reverse=False, error_log=None):
    """Generate lines from all server logs. A line is a triple of the timestamp (an aware datetime object), the prefix (info like the log level, depends on how old the log is), and the log message.
    
    Optional arguments:
    reverse -- Causes the log lines to be generated from newest to oldest instead of chronologically. Defaults to False.
    error_log -- a file-like object where any error messages and tracebacks are directed. Defaults to None, meaning no error logging.
    """
    if reverse:
        log_files = [os.path.join(config('paths')['server'], 'logs', 'latest.log')] + [os.path.join(config('paths')['server'], 'logs', logfilename) for logfilename in sorted(os.listdir(os.path.join(config('paths')['server'], 'logs')), reverse=True)] + [os.path.join(config('paths')['server'], 'server.log')]
    else:
        log_files = [os.path.join(config('paths')['server'], 'server.log')] + [os.path.join(config('paths')['server'], 'logs', logfilename) for logfilename in sorted(os.listdir(os.path.join(config('paths')['server'], 'logs')))] + [os.path.join(config('paths')['server'], 'logs', 'latest.log')]
    
    for log_file_name in log_files:
        if log_file_name.endswith('.log.gz'):
            open_func = gzip.open
        else:
            open_func = open
        try:
            with open_func(log_file_name) as logfile:
                for line in (reversed(list(logfile)) if reverse else logfile):
                    match = re.match('(' + regexes.timestamp + '|' + regexes.full_timestamp + ') ' + regexes.prefix + ' (.*)$', line)
                    if match:
                        if match.group(1).startswith('['):
                            log_date = date.today()
                            if re.match('\\d{4}-\\d{2}-\\d{2}', log_file_name[:10]):
                                log_date = log_file_name[:10]
                            yield regexes.strptime(log_date, match.group(1), tzinfo=timezone(timedelta(hours=config('utc_offset')))), match.group(2), match.group(3)
                        else:
                            yield datetime.strptime(match.group(1) + ' +0000', '%Y-%m-%d %H:%M:%S %z') , match.group(2), match.group(3)
                    else:
                        yield None, None, line.rstrip('\r\n')
        except GeneratorExit:
            raise StopIteration
        except:
            if error_log is not None:
                print('DEBUG] Exception reading logs:', file=error_log)
                traceback.print_exc(file=error_log)

def online_players(retry=True, allow_exceptions=False):
    found = False
    try:
        list = command('list')
    except socket.error:
        if allow_exceptions:
            raise
        return []
    if list is None:
        if allow_exceptions:
            raise ValueError('list is None')
        if retry:
            return online_players(retry=False)
        return []
    for line in list.splitlines():
        if found:
            match = re.match(regexes.timestamp + '|' + regexes.full_timestamp + ' \\[Server thread/INFO\\]: (' + regexes.player + '(, ' + regexes.player + ')*)?', line)
            if match:
                return [] if match.group(1) is None else match.group(1).split(', ')
        found = bool(re.match(regexes.timestamp + '|' + regexes.full_timestamp + ' \\[Server thread/INFO\\]: There are [0-9]+/[0-9]+ players online:' , line))
    # no player list in return
    if allow_exceptions:
        raise ValueError('no player list found')
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
    kwargs['start_message'] = kwargs.get('start_message', 'Server stopped. Restarting...')
    return start(*args, **kwargs)

def save_off(announce=True, reply=print):
    """Turn off automatic world saves, then force-save once.
    
    Optional arguments:
    announce -- Whether to announce in-game that saves are being disabled.
    reply -- This function is called with human-readable progress updates. Defaults to the built-in print function.
    """
    if status():
        reply('Minecraft is running... suspending saves')
        if announce:
            say('Server backup starting. Server going readonly...')
        command('save-off')
        command('save-all')
        subprocess.call(['sync'])
        time.sleep(10)
    else:
        reply('Minecraft is not running. Not suspending saves.')

def save_on(announce=True, reply=print):
    """Enable automatic world saves.
    
    Optional arguments:
    announce -- Whether to announce in-game that saves are being enabled.
    reply -- This function is called with human-readable progress updates. Defaults to the built-in print function.
    """
    if status():
        reply('Minecraft is running... re-enabling saves')
        command('save-on')
        if announce:
            say('Server backup ended. Server going readwrite...')
    else:
        reply('Minecraft is not running. Not resuming saves.')

def say(message, prefix=True):
    if prefix:
        command('say', [message])
    else:
        tellraw(message)

def start(*args, **kwargs):
    invocation = ['java', '-Xmx' + str(config('java_options')['max_heap']) + 'M', '-Xms' + str(config('java_options')['min_heap']) + 'M', '-XX:+UseConcMarkSweepGC', '-XX:+CMSIncrementalMode', '-XX:+CMSIncrementalPacing', '-XX:ParallelGCThreads=' + str(config('java_options')['cpu_count']), '-XX:+AggressiveOpts', '-Dlog4j.configurationFile=' + config('paths')['logConfig'], '-jar', config('paths')['service']] + config('java_options')['jar_options']
    reply = kwargs.get('reply', print)
    def _start(timeout=0.1):
        with open(os.path.devnull) as devnull:
            javapopen = subprocess.Popen(invocation, stdin=subprocess.PIPE, stdout=devnull, cwd=config('paths')['server'])
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
        notice = kwargs.get('notice', 'SERVER SHUTTING DOWN IN 10 SECONDS. Saving map...')
        if notice is not None:
            say(str(notice))
        command('save-all')
        time.sleep(10)
        command('stop')
        time.sleep(7)
        if kwargs.get('log_path'):
            with open(kwargs['log_path'], 'a') as loginslog:
                print(datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S') + ' @restart', file=loginslog) # logs in UTC
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

def update(version=None, snapshot=False, reply=print, log_path=None):
    """Download a different version of Minecraft and restart the server if it is running.
    
    Optional arguments:
    version -- If given, a version with this name will be downloaded. By default, the newest available version is downloaded.
    snapshot -- If version is given, this specifies whether the version is a development version. If no version is given, this specifies whether the newest stable version or the newest development version should be downloaded. Defaults to False.
    reply -- This function is called several times with a string argument representing update progress. Defaults to the built-in print function.
    log_path -- This is passed to the stop function if the server is stopped before the update.
    """
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
    if os.path.exists(os.path.join(config('paths')['jar'], 'minecraft_server.' + version + '.jar')):
        os.remove(os.path.join(config('paths')['jar'], 'minecraft_server.' + version + '.jar'))
    _download('https://s3.amazonaws.com/Minecraft.Download/versions/' + version + '/minecraft_server.' + version + '.jar', local_filename=os.path.join(config('paths')['jar'], 'minecraft_server.' + version + '.jar'))
    if 'client_versions' in config('paths'):
        os.makedirs(os.path.join(config('paths')['client_versions'], version), exist_ok=True)
        _download('https://s3.amazonaws.com/Minecraft.Download/versions/' + version + '/' + version + '.jar', local_filename=os.path.join(config('paths')['client_versions'], version, version + '.jar'))
    say('Server will be upgrading to ' + version_text + ' and therefore restart')
    time.sleep(5)
    was_running = status()
    stop(reply=reply, log_path=log_path)
    if os.path.lexists(config('paths')['service']):
        os.unlink(config('paths')['service'])
    os.symlink(os.path.join(config('paths')['jar'], 'minecraft_server.' + version + '.jar'), config('paths')['service'])
    if os.path.lexists(os.path.join(config('paths')['home'], 'home', 'client.jar')):
        os.unlink(os.path.join(config('paths')['home'], 'home', 'client.jar'))
    os.symlink(os.path.join(config('paths')['client_versions'], version, version + '.jar'), os.path.join(config('paths')['home'], 'home', 'client.jar'))
    if was_running:
        start(reply=reply, start_message='Server updated. Restarting...')
    return version, snapshot, version_text

def update_status(force=False):
    if force:
        players = online_players()
    else:
        try:
            players = online_players(allow_exceptions=True)
        except:
            try:
                with open(os.path.join(config('paths')['assets'], 'status.json')) as statusjson:
                    old_status = json.load(statusjson)
                players = old_status['list']
            except:
                players = []
    d = {
        'list': players,
        'on': status(),
        'version': version()
    }
    with open(os.path.join(config('paths')['assets'], 'status.json'), 'w') as statusjson:
        json.dump(d, statusjson, sort_keys=True, indent=4, separators=(',', ': '))

def update_whitelist(people_file=None):
    import lazyjson
    # get wanted whitelist from people file
    if people_file is None:
        people_file = config('paths')['people']
    whitelist = []
    by_name = []
    additional = config('whitelist').get('additional', [])
    if not config('whitelist').get('ignore_people', False):
        with open(people_file) as people_fobj:
            people = json.load(people_fobj)
            if isinstance(people, dict):
                people = people['people']
            for person in people:
                if not person.get('minecraft'):
                    continue
                if person.get('status', 'later') not in ['founding', 'later', 'postfreeze']:
                    continue
                if person.get('minecraftUUID'):
                    uuid = person['minecraftUUID'] if isinstance(person['minecraftUUID'], str) else format(person['minecraftUUID'], 'x')
                    if '-' not in uuid:
                        uuid = uuid[:8] + '-' + uuid[8:12] + '-' + uuid[12:16] + '-' + uuid[16:20] + '-' + uuid[20:]
                    whitelist.append({
                        'name': person['minecraft'],
                        'uuid': uuid
                    })
                else:
                    by_name.append(person['minecraft'])
    # write old whitelist
    old_whitelist_path = os.path.join(config('paths')['server'], 'white-list.txt')
    with open(old_whitelist_path, 'a'):
        os.utime(old_whitelist_path, None) # touch the file
    with open(old_whitelist_path, 'w') as whitelistfile:
        print('# DO NOT EDIT THIS FILE', file=whitelistfile)
        print('# it is automatically generated from ' + people_file, file=whitelistfile)
        print('# all changes will be lost on the next auto-update', file=whitelistfile)
        print(file=whitelistfile)
        if len(whitelist) > 0:
            print('# whitelisted by UUID:', file=whitelistfile)
            for person in whitelist:
                print(person['name'], file=whitelistfile)
        if len(by_name) > 0:
            print('# whitelisted by Minecraft nickname:', file=whitelistfile)
            for name in by_name:
                print(name, file=whitelistfile)
        if len(additional) > 0:
            print('# additional nicks generated from ' + CONFIG_FILE + ':', file=whitelistfile)
            for minecraft_nick in additional:
                print(minecraft_nick, file=whitelistfile)
    # write new whitelist
    new_whitelist_path = os.path.join(config('paths')['server'], 'whitelist.json')
    with open(new_whitelist_path, 'a'):
        os.utime(new_whitelist_path, None) # touch the file
    with open(new_whitelist_path, 'w') as whitelist_json:
        json.dump(whitelist, whitelist_json, sort_keys=True, indent=4, separators=(',', ': '))
    # apply changes to whitelist files
    command('whitelist', ['reload'])
    # add people with unknown UUIDs to new whitelist using the command
    for name in by_name + additional:
        command('whitelist', ['add', name])
    # update people file
    try:
        with open(os.path.join(config('paths')['server'], 'whitelist.json')) as whitelist_json:
            whitelist = json.load(whitelist_json)
    except ValueError:
        return
    people = lazyjson.File(config('paths')['people'])
    for whitelist_entry in whitelist:
        for person in people['people']:
            if person.get('minecraftUUID') == whitelist_entry['uuid']:
                if 'minecraft' in person and person['minecraft'] != whitelist_entry['name'] and person['minecraft'] not in person.get('minecraft_previous', []):
                    if 'minecraft_previous' in person:
                        person['minecraft_previous'].append(person['minecraft'])
                    else:
                        person['minecraft_previous'] = [person['minecraft']]
                person['minecraft'] = whitelist_entry['name']
            elif person.get('minecraft') == whitelist_entry['name'] and 'minecraftUUID' not in person:
                person['minecraftUUID'] = whitelist_entry['uuid']

def version():
    for _, _, line in log(reverse=True):
        match = re.match('Starting minecraft server version (.*)', line)
        if match:
            return match.group(1)

def whitelist_add(id, minecraft_nick=None, minecraft_uuid=None, people_file='/opt/wurstmineberg/config/people.json', person_status='postfreeze', invited_by=None):
    """Add a new person to people.json and reload the whitelist.
    
    Required arguments:
    id -- The person's “Wurstmineberg ID” (the value used in the "id" field of people.json). Must match /[a-z][0-9a-z]{1,15}/.
    
    Optional arguments:
    minecraft_nick -- If given, this will be added to the people.json entry as the value of the "minecraft" field.
    minecraft_uuid -- If given, this will be added to the people.json entry as the value of the "minecraftUUID" field.
    people_file -- The path to people.json. Defaults to /opt/wurstmineberg/config/people.json
    person_status -- This will be added to the people.json entry as the value of the "status" field, determining whether or not the person will be on the whitelist. Defaults to postfreeze.
    invited_by -- The person who invited the new person. May be a Wurstmineberg ID or a wurstminebot.nicksub.Person object. If given, the inviting person will be noted in the invitee's people.json entry.
    """
    with open(people_file) as f:
        people = json.load(f)
    if isinstance(people, dict):
        people = people['people']
    for person in people:
        if person['id'] == id:
            if person['status'] == 'invited' and person_status != 'invited':
                person['join_date'] = datetime.utcnow().strftime('%Y-%m-%d')
                if hasattr(invited_by, 'id'):
                    person['invitedBy'] = invited_by.id
                elif invited_by is not None:
                    person['invitedBy'] = invited_by
                if minecraft_nick is not None:
                    person['minecraft'] = minecraft_nick
                if minecraft_uuid is not None:
                    person['minecraftUUID'] = minecraft_uuid
                person['status'] = person_status
                break
            else:
                raise ValueError('A person with this id already exists')
    else:
        new_person = {
            'id': id,
            'join_date': datetime.utcnow().strftime('%Y-%m-%d'),
            'options': {
                'show_inventory': True
            },
            'status': person_status
        }
        if hasattr(invited_by, 'id'):
            new_person['invitedBy'] = invited_by.id
        elif invited_by is not None:
            new_person['invitedBy'] = invited_by
        if minecraft_nick is not None:
            new_person['minecraft'] = minecraft_nick
        if minecraft_uuid is not None:
            new_person['minecraftUUID'] = minecraft_uuid
        people.append(new_person)
    with open(people_file, 'w') as people_fobj:
        json.dump({'people': people}, people_fobj, sort_keys=True, indent=4, separators=(',', ': '))
    update_whitelist(people_file=people_file)

def wiki_version_link(version):
    if not isinstance(version, str):
        return 'http://minecraft.gamepedia.com/Version_history'
    else:
        try:
            minecraft_wiki_result = requests.get('http://minecraft.gamepedia.com/api.php?format=json&action=query&titles=' + urllib.parse.quote(version)).json()
            if 'query' in minecraft_wiki_result and 'pages' in minecraft_wiki_result['query']:
                for page_id, page_info in minecraft_wiki_result['query']['pages'].items():
                    if 'missing' in page_info:
                        return 'http://minecraft.gamepedia.com/Version_history' + ('/Development_versions#' if 'pre' in version or version[2] == 'w' else '#') + urllib.parse.quote(version)
                    else:
                        return 'http://minecraft.gamepedia.com/' + re.sub(' ', '_', page_info['title'])
            return 'http://minecraft.gamepedia.com/Version_history' + ('/Development_versions#' if 'pre' in version or version[2] == 'w' else '#') + urllib.parse.quote(version)
        except:
            return 'http://minecraft.gamepedia.com/Version_history' + ('/Development_versions#' if 'pre' in version or version[2] == 'w' else '#') + urllib.parse.quote(version)

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
