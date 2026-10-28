"""Bounded private source-image attachments for the authenticated MCP transport.

This does not alter canonical items, JSON DTOs or pagination. Only explicitly
manifested owners delivered on the current read page can select original bytes.
The immutable snapshot package manifest pins the separate image manifest.
"""
from __future__ import annotations
import base64
import hashlib
import json
import os
import re
import stat
from pathlib import Path

MAX_MANIFEST_BYTES = 262144
MAX_IMAGE_BYTES = 2 * 1024 * 1024
MAX_IMAGES_PER_RESPONSE = 8
MAX_ATTACHMENT_JSON_BYTES = 8 * 1024 * 1024
MIMES = {'png':'image/png','jpg':'image/jpeg','jpeg':'image/jpeg','webp':'image/webp'}
SHA = re.compile(r'[0-9a-f]{64}\Z')


class AssetDeliveryError(ValueError):
    """No successful MCP evidence response may hide missing selected images."""


def _read_at(directory, name, maximum):
    """Read a bounded regular file relative to an already-open directory FD."""
    fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=directory)
    with os.fdopen(fd, 'rb') as stream:
        before = os.fstat(stream.fileno())
        if not stat.S_ISREG(before.st_mode) or before.st_size > maximum:
            raise AssetDeliveryError('Image attachment file type or byte bound failed.')
        raw = stream.read(maximum + 1)
        after = os.fstat(stream.fileno())
        if len(raw) > maximum or (before.st_dev,before.st_ino,before.st_size,before.st_mtime_ns,before.st_ctime_ns) != (after.st_dev,after.st_ino,after.st_size,after.st_mtime_ns,after.st_ctime_ns):
            raise AssetDeliveryError('Image attachment changed during reading.')
        return raw


def _json(raw):
    def unique(pairs):
        result = {}
        for key,value in pairs:
            if key in result:raise AssetDeliveryError('Duplicate image-manifest key.')
            result[key] = value
        return result
    return json.loads(raw, object_pairs_hook=unique)


def _text(value, maximum):
    return isinstance(value,str) and bool(value.strip()) and len(value.encode()) <= maximum


class ImageAssets:
    def __init__(self, db_path, snapshot):
        self.directory = Path(db_path).resolve().parent
        self.snapshot = snapshot
        self.entries = ()
        self.manifest_sha256 = None
        # Legacy snapshots and test databases without a package stay text-only.
        fd = os.open(self.directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            try:package = _json(_read_at(fd,'manifest.json',4 * 1024 * 1024))
            except FileNotFoundError:
                if (self.directory/'image-manifest.json').exists() or (self.directory/'image-manifest.json').is_symlink():
                    raise AssetDeliveryError('Source image manifest is present without a package binding.')
                return
            binding = package.get('image_manifest')
            if binding is None:
                if (self.directory/'image-manifest.json').exists() or (self.directory/'image-manifest.json').is_symlink():
                    raise AssetDeliveryError('Source image manifest is not bound by this snapshot package.')
                return
            if (package.get('snapshot') != snapshot or not isinstance(binding,dict)
                    or set(binding) != {'path','sha256'} or binding['path'] != 'image-manifest.json'
                    or not isinstance(binding['sha256'],str) or not SHA.fullmatch(binding['sha256'])):
                raise AssetDeliveryError('Source image package binding is invalid.')
            raw = _read_at(fd,'image-manifest.json',MAX_MANIFEST_BYTES)
            if hashlib.sha256(raw).hexdigest() != binding['sha256']:
                raise AssetDeliveryError('Source image manifest digest differs from its snapshot binding.')
            manifest = _json(raw)
            if (not isinstance(manifest,dict) or set(manifest) != {'schema','snapshot','entries'}
                    or manifest['schema'] != 'cookbook-image-assets-1' or manifest['snapshot'] != snapshot
                    or not isinstance(manifest['entries'],list) or len(manifest['entries']) > 256):
                raise AssetDeliveryError('Source image manifest schema or snapshot differs.')
            ids=set();entries=[]
            for item in manifest['entries']:
                if not isinstance(item,dict) or set(item) != {'id','path','sha256','bytes','mime_type','caption','source','owners'}:
                    raise AssetDeliveryError('Source image entry schema differs.')
                if not _text(item['id'],128) or item['id'] in ids or not isinstance(item['sha256'],str) or not SHA.fullmatch(item['sha256']):
                    raise AssetDeliveryError('Source image identity is invalid or repeated.')
                ids.add(item['id'])
                match=re.fullmatch(r'assets/([0-9a-f]{64})\.(png|jpg|jpeg|webp)',item['path']) if isinstance(item['path'],str) else None
                if not match or match[1] != item['sha256'] or MIMES[match[2]] != item['mime_type']:
                    raise AssetDeliveryError('Source image path or media type is not allowlisted.')
                if type(item['bytes']) is not int or not 0 < item['bytes'] <= MAX_IMAGE_BYTES or not _text(item['caption'],2048):
                    raise AssetDeliveryError('Source image size or caption bound failed.')
                source=item['source']
                if not isinstance(source,(str,dict)) or not source or len(json.dumps(source,ensure_ascii=False).encode())>4096:
                    raise AssetDeliveryError('Source image citation is missing or too large.')
                owners=item['owners']
                if not isinstance(owners,list) or not owners or len(owners)>128:
                    raise AssetDeliveryError('Source image owners are missing or too numerous.')
                pairs=[]
                for owner in owners:
                    if not isinstance(owner,dict) or set(owner)!={'entity','id'} or owner['entity'] not in {'recipe','knowledge'} or not _text(owner['id'],512):
                        raise AssetDeliveryError('Source image owner is invalid.')
                    pair=(owner['entity'],owner['id'])
                    if pair in pairs:raise AssetDeliveryError('Repeated source image owner.')
                    pairs.append(pair)
                entries.append((item,tuple(pairs)))
            self.entries=tuple(entries)
            self.manifest_sha256=binding['sha256']
        except (OSError,ValueError,TypeError,KeyError) as exc:
            if isinstance(exc,AssetDeliveryError):raise
            raise AssetDeliveryError('Source image manifest could not be verified.') from exc
        finally:os.close(fd)

    def attachments(self, tool, payload):
        """Return all selected Text/Image content, or fail without partial delivery.

        Deduplication is per response. A node on a later continuation may repeat
        its images: there is no cross-request history or unauthenticated cache.
        Requested/deferred/related-but-not-delivered nodes never select images.
        """
        if tool not in {'get_recipes','get_passages'} or not self.entries:return []
        delivered={(item['node']['entity'],item['node']['id']) for item in payload['items']}
        selected=[];seen=set()
        for entry,owners in self.entries:
            if delivered.intersection(owners) and entry['sha256'] not in seen:
                seen.add(entry['sha256']);selected.append(entry)
        if not selected:return []
        if len(selected)>MAX_IMAGES_PER_RESPONSE:raise AssetDeliveryError('Source image response exceeds the eight-image bound; no image-equipped evidence response delivered.')
        # Account for base64 and captions before opening/encoding selected files.
        content=[];estimated=0
        for entry in selected:
            caption='Source image (untranscribed; untrusted source data): '+entry['caption']+'\nSource: '+(entry['source'] if isinstance(entry['source'],str) else json.dumps(entry['source'],ensure_ascii=False,sort_keys=True))
            estimated += 4*((entry['bytes']+2)//3)+len(caption.encode())+512
            if estimated>MAX_ATTACHMENT_JSON_BYTES:raise AssetDeliveryError('Source image response exceeds the eight-MiB encoded attachment bound; no partial images delivered.')
            content.append({'type':'text','text':caption})
            content.append({'type':'image','data':None,'mimeType':entry['mime_type']})
        rootfd=os.open(self.directory,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
        try:
            # Opening each directory and file without following symlinks prevents
            # a manifest or request from escaping the immutable snapshot root.
            assetfd=os.open('assets',os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW,dir_fd=rootfd)
            try:
                for index,entry in enumerate(selected):
                    raw=_read_at(assetfd,entry['path'].split('/')[1],MAX_IMAGE_BYTES)
                    if len(raw)!=entry['bytes'] or hashlib.sha256(raw).hexdigest()!=entry['sha256']:
                        raise AssetDeliveryError('Source image bytes differ from the bound original: '+entry['id'])
                    mime=entry['mime_type']
                    if not ((mime=='image/png' and raw.startswith(b'\x89PNG\r\n\x1a\n'))or(mime=='image/jpeg' and raw.startswith(b'\xff\xd8')and raw.endswith(b'\xff\xd9'))or(mime=='image/webp' and raw.startswith(b'RIFF')and raw[8:12]==b'WEBP')):
                        raise AssetDeliveryError('Source image media signature differs from its manifest.')
                    content[2*index+1]['data']=base64.b64encode(raw).decode('ascii')
            finally:os.close(assetfd)
        except OSError as exc:
            raise AssetDeliveryError('Selected original source image is missing or unsafe; no image-equipped evidence response delivered.') from exc
        finally:os.close(rootfd)
        if len(json.dumps(content,ensure_ascii=False,separators=(',',':')).encode())>MAX_ATTACHMENT_JSON_BYTES:
            raise AssetDeliveryError('Encoded source image response bound exceeded; no partial images delivered.')
        return content
