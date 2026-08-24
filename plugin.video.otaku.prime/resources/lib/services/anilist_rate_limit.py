# -*- coding: utf-8 -*-
"""Process-wide pacing for every direct AniList GraphQL client."""
import threading
import time


class AniListRateLimiter:
    def __init__(self, requests_per_minute=28):
        self.interval=60.0/max(1,int(requests_per_minute))
        self._lock=threading.Lock(); self._next_request=0.0

    def wait(self):
        with self._lock:
            now=time.monotonic(); delay=max(0.0,self._next_request-now)
            if delay: time.sleep(delay)
            self._next_request=time.monotonic()+self.interval

    @staticmethod
    def retry_delay(error,default=60):
        headers=getattr(error,"headers",None)
        value=headers.get("Retry-After") if headers else None
        try: return max(1,min(300,int(value)))
        except (TypeError,ValueError): return int(default)


ANILIST_RATE_LIMITER=AniListRateLimiter()
