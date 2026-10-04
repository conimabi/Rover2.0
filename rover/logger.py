'''
简易线程安全日志：同时写控制台与文件（UTF-8），带时间戳。
Logger 实例本身可调用（__call__），因此能直接当作连接层的 logger 参数传入。
'''
import threading
import time


class Logger(object):
    def __init__(self, path=None, echo=True):
        self.echo = echo
        self._lock = threading.Lock()
        self._f = open(path, 'a', encoding='utf-8') if path else None

    def __call__(self, msg):
        line = '%s  %s' % (time.strftime('%Y-%m-%d %H:%M:%S'), msg)
        with self._lock:
            if self._f:
                self._f.write(line + '\n')
                self._f.flush()
            if self.echo:
                print(line, flush=True)

    def close(self):
        with self._lock:
            if self._f:
                self._f.close()
                self._f = None