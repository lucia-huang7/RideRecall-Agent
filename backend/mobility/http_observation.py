"""Bounded-label HTTP metrics and payload-free correlated diagnostic events."""
import json
import logging
from pathlib import Path
import traceback

from prometheus_client import CollectorRegistry, Counter, Gauge, Histogram, ProcessCollector, GCCollector, generate_latest

LOGGER=logging.getLogger('kinderuber.http')
METHODS={'GET','POST','PUT','PATCH','DELETE','HEAD','OPTIONS'}


def configure_logging():
    if not LOGGER.handlers:
        handler=logging.StreamHandler()
        handler.setFormatter(logging.Formatter('%(message)s'))
        LOGGER.addHandler(handler)
    LOGGER.setLevel(logging.INFO)
    LOGGER.propagate=False


class HttpObservation:
    def __init__(self):
        self.registry=CollectorRegistry()
        ProcessCollector(namespace='kinderuber',registry=self.registry)
        GCCollector(registry=self.registry)
        self.requests=Counter('kinderuber_http_requests_total','Prepared HTTP responses',['method','route','status'],registry=self.registry)
        self.latency=Histogram('kinderuber_http_request_seconds','HTTP response preparation duration',['method','route'],buckets=(.005,.025,.1,.5,1,5,15,35,60),registry=self.registry)
        self.active=Gauge('kinderuber_http_active','Active HTTP requests',registry=self.registry)

    def finish(self,correlation_id,method,route,status,elapsed,error=None):
        method=method if method in METHODS else 'OTHER'
        # Routes come from matched application definitions, never raw paths or queries.
        route=route or 'unmatched'
        self.active.dec()
        self.requests.labels(method,route,str(status)).inc()
        self.latency.labels(method,route).observe(elapsed)
        event=dict(event='http_request',correlation_id=correlation_id,method=method,route=route,status=status,duration_ms=round(elapsed*1000,3))
        if error is not None:
            event['error_type']=type(error).__name__
            event['frames']=[dict(module=Path(frame.filename).name,function=frame.name,line=frame.lineno) for frame in traceback.extract_tb(error.__traceback__)[-8:]]
        LOGGER.log(logging.ERROR if status>=500 else logging.INFO,json.dumps(event,separators=(',',':')))

    def render(self):return generate_latest(self.registry)
