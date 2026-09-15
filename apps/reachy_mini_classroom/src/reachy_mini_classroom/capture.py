"""Logical timing only: no audio files."""
import math
import struct
RATE = 16000

class BlockClock:
    def __init__(self, block_seconds=1200, silence_seconds=1.5, threshold=0.02):
        self.limit, self.silence_limit, self.threshold = block_seconds, silence_seconds, threshold
        self.samples = self.silent = 0
        self.level = 0.0

    @property
    def elapsed(self): return self.samples / RATE

    def feed(self, pcm):
        if not pcm: return False
        if len(pcm) % 2: raise ValueError('PCM incompleto')
        count = len(pcm)//2
        self.level = math.sqrt(sum(v[0]**2 for v in struct.iter_unpack('<h', pcm))/count)/32768
        self.samples += count
        self.silent = self.silent + count if self.level <= self.threshold else 0
        return self.elapsed >= self.limit and self.silent/RATE >= self.silence_limit
