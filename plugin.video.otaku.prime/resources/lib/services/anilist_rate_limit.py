# -*- coding: utf-8 -*-
"""Process-wide pacing for every direct AniList GraphQL client."""
import threading
import time


class AniListRateLimiter:
    def __init__(self, requests_per_minute=28):
        self.interval=60.0/max(1,int(requests_per_minute))
        self._lock=threading.Lock(); self._next_request=0.0

    def wait(self,halt_requested=None):
        """Pace requests without holding the lock or hiding a Kodi abort."""
        halt_requested=halt_requested or (lambda:False)
        while True:
            if halt_requested(): return False
            with self._lock:
                now=time.monotonic(); delay=max(0.0,self._next_request-now)
                if delay<=0:
                    self._next_request=now+self.interval
                    return True
            deadline=time.monotonic()+delay
            while time.monotonic()<deadline:
                if halt_requested(): return False
                time.sleep(min(0.1,max(0.0,deadline-time.monotonic())))

    @staticmethod
    def retry_delay(error,default=60):
        headers=getattr(error,"headers",None)
        value=headers.get("Retry-After") if headers else None
        try: return max(1,min(300,int(value)))
        except (TypeError,ValueError): return int(default)


ANILIST_RATE_LIMITER=AniListRateLimiter()
