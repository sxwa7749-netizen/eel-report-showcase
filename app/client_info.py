"""访问元数据：仅保留设备类别，本地离线地址库查询不向外发送IP。"""
import ipaddress
from functools import lru_cache
from pathlib import Path
import struct


def city_lookup(address):
    """本机 DB-IP 月度库；中国大陆优先保留原库的中文数据。"""
    try:
        import maxminddb
    except ImportError:
        return None
    try:
        file=Path('/var/lib/demo-monitor/geo/dbip-city-lite.mmdb')
        with maxminddb.open_database(str(file),mode=maxminddb.MODE_MMAP) as reader:
            row=reader.get(str(address)) or {}
        country=row.get('country',{})
        if country.get('iso_code')=='CN':return None
        labels=[]
        # 海外移动网络细到街区的结果常有明显偏差，仅展示国家与省县。
        for part in (country,*row.get('subdivisions',[])[:1]):
            names=part.get('names',{})
            label=names.get('zh-CN') or names.get('en')
            if country.get('iso_code')=='JP':
                label={'Kanagawa':'神奈川县','Tokyo':'东京都','Osaka':'大阪府',
                       'Tochigi':'栃木县','Chiba':'千叶县','Saitama':'埼玉县',
                       'Hokkaido':'北海道','Kyoto':'京都府'}.get(label,label)
            if label and label not in labels:labels.append(label)
        return ' · '.join(labels) or None
    except (OSError,ValueError,maxminddb.InvalidDatabaseError):
        return None


def device_type(agent):
    text=str(agent or '')[:2048].lower()
    if not text:return '未知设备（此前未采集）'
    if any(v in text for v in ('bot','spider','crawler','headless')):return '自动程序 / 爬虫'
    if any(v in text for v in ('curl/','python-','go-http-client','wget/','postman')):return '接口工具 / 自动程序'
    if 'ipad' in text or ('android' in text and 'mobile' not in text):device='平板'
    elif any(v in text for v in ('iphone','ipod','mobile','windows phone')):device='手机'
    elif any(v in text for v in ('windows','macintosh','linux','x11')):device='电脑'
    else:device='未知设备'
    system='iOS / iPadOS' if any(v in text for v in ('iphone','ipad','ipod')) else 'Android' if 'android' in text else 'Windows' if 'windows' in text else 'macOS' if 'macintosh' in text else 'Linux' if 'linux' in text else ''
    browser='Edge' if 'edg' in text else 'Chrome' if any(v in text for v in ('chrome','crios')) else 'Firefox' if any(v in text for v in ('firefox','fxios')) else 'Safari' if 'safari' in text else ''
    return ' · '.join(v for v in (device,system,browser) if v)


@lru_cache(maxsize=4096)
def geo_lookup(ip):
    try:
        address=ipaddress.ip_address(ip)
        if address.version==6 and address.ipv4_mapped:address=address.ipv4_mapped
        if not address.is_global:return '内网 / 保留地址'
        city=city_lookup(address)
        if city:return city
        file=Path('/var/lib/demo-monitor/geo')/f'ip2region_v{address.version}.xdb'
        with file.open('rb') as stream:
            header=stream.read(256)
            version=struct.unpack_from('<H',header)[0]
            family=struct.unpack_from('<H',header,16)[0] if version>=3 else 4
            if version not in (2,3) or family!=address.version:return '地址库版本不匹配'
            packed=address.packed;width=len(packed);size=width*2+6
            stream.seek(256+int.from_bytes(packed[:2],'big')*8)
            first,last=struct.unpack('<II',stream.read(8))
            if not first or not last:return '未知地区'
            low,high=0,(last-first)//size
            while low<=high:
                mid=(low+high)//2;stream.seek(first+mid*size);block=stream.read(size)
                if len(block)!=size:break
                begin,end=block[:width],block[width:width*2]
                if address.version==4:begin,end=begin[::-1],end[::-1]
                if packed<begin:high=mid-1
                elif packed>end:low=mid+1
                else:
                    length,pointer=struct.unpack_from('<HI',block,width*2)
                    if not 0<length<4096:return '未知地区'
                    stream.seek(pointer);parts=stream.read(length).decode('utf-8').split('|')
                    country={'CN':'中国','JP':'日本','HK':'中国香港','TW':'中国台湾','MO':'中国澳门','US':'美国','SG':'新加坡','KR':'韩国'}.get(parts[-1],parts[0])
                    labels=[]
                    for label in (country,*(parts[1:3] if parts[-1]=='CN' else parts[1:2])):
                        if label and label not in ('0','-') and label not in labels:labels.append(label)
                    return ' · '.join(labels) or '未知地区'
    except (OSError,ValueError,struct.error,UnicodeError,IndexError):
        return '未知地区（地址库未就绪）'
    return '未知地区'
