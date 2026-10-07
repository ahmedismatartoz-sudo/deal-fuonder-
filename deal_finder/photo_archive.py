"""Retain original candidate photographs privately; source links are provenance.

Download from approved marketplace CDNs only, without redirects or login.
Failure never becomes an archived photo. Cover photos get priority over galleries.
"""
import hashlib
import os
import re
import time
from datetime import datetime, timezone, timedelta
from urllib.parse import urlsplit
from urllib.request import Request, build_opener
from urllib.error import HTTPError, URLError
from .agents.photo_identity import public_url, NoRedirect

MAX_PHOTO_BYTES=2*1024*1024
MAX_STORED_BYTES=32*1024*1024
PERMANENT=('unapproved_image_host','invalid_image','too_large','http_401','http_403','http_404','http_410')


def approved_url(value):
    public_url(value)
    host=urlsplit(value).hostname.casefold()
    if not any(host==root or host.endswith('.'+root) for root in ('autoscout24.net','fbcdn.net')):
        raise ValueError('unapproved_image_host')
    return value


def image_type(content):
    if content.startswith(b'\xff\xd8\xff') and content.endswith(b'\xff\xd9'): return 'image/jpeg'
    if content.startswith(b'\x89PNG\r\n\x1a\n') and b'IEND' in content[-16:]: return 'image/png'
    if (len(content)>=12 and content[:4]==b'RIFF' and content[8:12]==b'WEBP'
            and int.from_bytes(content[4:8],'little')+8==len(content)): return 'image/webp'
    raise ValueError('invalid_image')


def download(value):
    approved_url(value)
    request=Request(value,headers={'User-Agent':'DealFinderPhotoArchive/1.0','Accept':'image/jpeg,image/png,image/webp'})
    try:
        with build_opener(NoRedirect()).open(request,timeout=10) as response:
            if response.status!=200: raise ValueError('http_'+str(response.status))
            content=response.read(MAX_PHOTO_BYTES+1)
            if len(content)>MAX_PHOTO_BYTES: raise ValueError('too_large')
            mime=image_type(content)
            if response.headers.get_content_type()!=mime: raise ValueError('invalid_image')
            return content,mime
    except HTTPError as error:
        raise ValueError('http_'+str(error.code)) from None
    except (URLError,TimeoutError):
        raise ValueError('network_unavailable') from None


class PhotoArchive:
    def __init__(self,db):
        self.db=db
        if db.dialect=='sqlite':
            db.executescript('''CREATE TABLE IF NOT EXISTS photo_assets (
                sha TEXT PRIMARY KEY,mime TEXT NOT NULL,content BLOB NOT NULL,byte_count INTEGER NOT NULL,saved_at TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS photo_references (
                source TEXT NOT NULL,source_id TEXT NOT NULL,observed_at TEXT NOT NULL,ordinal INTEGER NOT NULL,
                original_url TEXT NOT NULL,attempt INTEGER NOT NULL,sha TEXT REFERENCES photo_assets(sha),
                saved_at TEXT NOT NULL,error_code TEXT,
                PRIMARY KEY(source,source_id,observed_at,ordinal,attempt));
                CREATE INDEX IF NOT EXISTS photo_original_url ON photo_references(original_url,saved_at DESC);''')

    def manifest(self,source,source_id,observed_at):
        rows=self.db.execute('''SELECT r.ordinal,r.original_url,r.sha,a.mime,a.byte_count FROM photo_references r
            JOIN photo_assets a ON a.sha=r.sha WHERE r.source=? AND r.source_id=? AND r.observed_at=?
            ORDER BY r.ordinal,r.attempt''',(source,source_id,observed_at)).fetchall()
        return [dict(index=i,original_url=url,sha=sha,mime=mime,bytes=size,archived=True,
                     retained_url='/archive/photos/'+sha) for i,url,sha,mime,size in rows]

    def retain(self,source,source_id,observed_at,ordinal,url,*,fetch=download,as_of=None):
        now=as_of or datetime.now(timezone.utc)
        # Bind a saved image to its original immutable source observation.
        field="payload->'image_urls'" if self.db.dialect=='postgres' else "json_extract(payload,'$.image_urls')"
        raw=self.db.execute('SELECT '+field+' FROM listing_events WHERE source=? AND source_id=? AND observed_at=?',
                            (source,source_id,observed_at)).fetchone()
        images=self.db.json_decode(raw[0]) if raw and raw[0] is not None else []
        if type(ordinal) is not int or ordinal<0 or ordinal>=len(images) or images[ordinal]!=url:
            raise ValueError('Photo must match the archived source observation')
        previous=self.db.execute('''SELECT attempt,sha,error_code,saved_at FROM photo_references
            WHERE source=? AND source_id=? AND observed_at=? AND ordinal=? ORDER BY attempt DESC LIMIT 1''',
            (source,source_id,observed_at,ordinal)).fetchone()
        if previous and (previous[1] or previous[0]>=3 or previous[2] in PERMANENT):
            return dict(status='already_archived' if previous[1] else 'already_unavailable',sha=previous[1])
        if previous and now-datetime.fromisoformat(previous[3])<timedelta(minutes=15):
            return dict(status='retry_later')
        attempt=previous[0]+1 if previous else 1
        existing=self.db.execute('SELECT sha FROM photo_references WHERE original_url=? AND sha IS NOT NULL ORDER BY saved_at DESC LIMIT 1',
                                 (url,)).fetchone()
        sha=existing[0] if existing else None
        error=None;content=None;mime=None
        if not sha:
            try:
                approved_url(url)
                used=self.db.execute('SELECT COALESCE(sum(byte_count),0) FROM photo_assets').fetchone()[0]
                if used>=MAX_STORED_BYTES: return dict(status='storage_limit',saved=False)
                content,mime=fetch(url)
                if not content or len(content)>MAX_PHOTO_BYTES: raise ValueError('too_large')
                if image_type(content)!=mime: raise ValueError('invalid_image')
                sha=hashlib.sha256(content).hexdigest()
            except ValueError as failure:
                message=str(failure)
                error=message if message in PERMANENT or re.fullmatch(r'http_\d{3}',message) or message=='network_unavailable' else 'download_rejected'
        with self.db:
            self.db.batch_lock('retained-photo-storage')
            if content is not None and sha:
                used=self.db.execute('SELECT COALESCE(sum(byte_count),0) FROM photo_assets').fetchone()[0]
                duplicate=self.db.execute('SELECT 1 FROM photo_assets WHERE sha=?',(sha,)).fetchone()
                if not duplicate and used+len(content)>MAX_STORED_BYTES:
                    return dict(status='storage_limit',saved=False)
                self.db.execute('INSERT INTO photo_assets VALUES (?,?,?,?,?) ON CONFLICT(sha) DO NOTHING',
                    (sha,mime,content,len(content),now.isoformat()))
            self.db.execute('INSERT INTO photo_references VALUES (?,?,?,?,?,?,?,?,?) ON CONFLICT DO NOTHING',
                (source,source_id,observed_at,ordinal,url,attempt,sha,now.isoformat(),error))
        return dict(status='archived' if sha else 'unavailable',sha=sha,error_code=error,saved=bool(sha))

    def step(self,report,*,limit=2,fetch=download):
        jobs=[]
        for p in report.get('candidates',[]):
            current=self.db.execute('SELECT observed_at,active FROM listing_events WHERE source=? AND source_id=? ORDER BY observed_at DESC LIMIT 1',
                                    (p['source'],p['source_id'])).fetchone()
            if not current or current[0]!=p['observed_at'] or not current[1]: continue
            field="payload->'image_urls'" if self.db.dialect=='postgres' else "json_extract(payload,'$.image_urls')"
            raw=self.db.execute('SELECT '+field+' FROM listing_events WHERE source=? AND source_id=? AND observed_at=?',
                                (p['source'],p['source_id'],p['observed_at'])).fetchone()
            images=self.db.json_decode(raw[0]) if raw and raw[0] is not None else []
            saved={x['index'] for x in self.manifest(p['source'],p['source_id'],p['observed_at'])}
            for i,url in enumerate(images):
                if i not in saved: jobs.append((i,p,url))
        jobs.sort(key=lambda x:x[0])
        out=[]
        for i,p,url in jobs:
            value=self.retain(p['source'],p['source_id'],p['observed_at'],i,url,fetch=fetch)
            if value['status'] in ('already_archived','already_unavailable','retry_later'): continue
            out.append(value)
            if value['status']=='storage_limit' or len(out)>=limit: break
        return dict(photo_archive='progress' if out else 'idle',saved=sum(bool(x.get('saved')) for x in out),
                    unavailable=sum(x['status']=='unavailable' for x in out),storage_limit=any(x['status']=='storage_limit' for x in out))


class BackgroundPhotoArchive:
    def __init__(self,path): self.path=path;self.last_poll=None
    def step(self,run_id):
        if not run_id or self.last_poll is not None and time.monotonic()-self.last_poll<5: return None
        self.last_poll=time.monotonic()
        from .archive import Archive
        archive=None
        try:
            archive=Archive(self.path)
            row=archive.db.execute('SELECT payload FROM price_test_reports WHERE run_id=?',(run_id,)).fetchone()
            if not row:
                from .price_memory import SCREENING_VERSION
                field="payload->>'screening_version'" if archive.db.dialect=='postgres' else "json_extract(payload,'$.screening_version')"
                row=archive.db.execute('SELECT payload FROM price_test_reports WHERE '+field+'=? ORDER BY as_of DESC LIMIT 1',(SCREENING_VERSION,)).fetchone()
            candidates=list((archive.db.json_decode(row[0]) if row else {}).get('candidates',[]))
            apparent=[]
            if os.getenv('DEAL_FINDER_PHOTO_OPPORTUNITY_REVIEW_ENABLED')=='1':
                field="p.payload#>>'{collection_price_screen,status}'" if archive.db.dialect=='postgres' else "json_extract(p.payload,'$.collection_price_screen.status')"
                rows=archive.db.execute("""SELECT p.source,p.source_id,p.observed_at FROM price_observations p
                    WHERE p.active=? AND p.observed_at>=? AND """+field+"""=? AND NOT EXISTS (
                        SELECT 1 FROM price_observations n WHERE n.source=p.source AND n.source_id=p.source_id
                            AND n.observed_at>p.observed_at) ORDER BY p.observed_at DESC LIMIT 20""",
                        (True,(datetime.now(timezone.utc)-timedelta(days=7)).isoformat(),'apparent_opportunity')).fetchall()
                apparent=[dict(source=s,source_id=i,observed_at=t) for s,i,t in rows]
                candidates.extend(apparent)
            candidates=list({(p['source'],p['source_id'],p['observed_at']):p for p in candidates}.values())
            result=PhotoArchive(archive.db).step(dict(candidates=candidates),fetch=download)
            if apparent:
                from .queue import Queue
                from .price_memory import priority_observation_batch
                from .agents.enrichment import listing_input
                queue=Queue(self.path)
                try:
                    queued=0
                    for p in apparent:
                        batch=priority_observation_batch(p)
                        if queue.db.execute('SELECT 1 FROM batches WHERE batch_id=?',(batch,)).fetchone():continue
                        latest=archive.db.execute('SELECT observed_at,url,payload,active FROM listing_events WHERE source=? AND source_id=? ORDER BY observed_at DESC LIMIT 1',(p['source'],p['source_id'])).fetchone()
                        if not latest or not latest[3] or latest[0]!=p['observed_at']:continue
                        queue.submit(batch,[dict(task='archive_enrichment',listing=listing_input(p['source'],p['source_id'],p['observed_at'],latest[1],archive.db.json_decode(latest[2])))])
                        queued+=1
                    result['queued_photo_reviews']=queued
                finally:queue.close()
            return result if result['photo_archive']!='idle' or result.get('queued_photo_reviews') else None
        except Exception as error:
            return dict(photo_archive='retry_later',error_type=type(error).__name__)
        finally:
            if archive: archive.close()
