"""Public YouTube search, enabled only by local user configuration."""
import json
import re

import requests


def parse_results(html):
    match = re.search(r'(?:var\s+ytInitialData\s*=|window\["ytInitialData"\]\s*=|ytInitialData\s*=)\s*', html)
    if not match:
        raise RuntimeError('YouTube no devolvió resultados legibles; puede requerir consentimiento o limitar la búsqueda.')
    data, _ = json.JSONDecoder().raw_decode(html[match.end():])
    result = []
    seen = set()
    def walk(node):
        if isinstance(node, dict):
            item = node.get('videoRenderer')
            if isinstance(item, dict):
                video_id = item.get('videoId', '')
                title = item.get('title', {})
                title = title.get('simpleText') or ''.join(part.get('text', '') for part in title.get('runs', []))
                if re.fullmatch(r'[\w-]{11}', video_id) and title and video_id not in seen:
                    seen.add(video_id)
                    result.append({'url': 'https://www.youtube.com/watch?v=' + video_id,
                                   'title': title, 'kind': 'video'})
            for key, child in node.items():
                if key not in ('adSlotRenderer', 'promotedSparklesWebRenderer', 'promotedVideoRenderer'):
                    walk(child)
        elif isinstance(node, list):
            for child in node:
                walk(child)
    walk(data)
    return result


def search(topic):
    query = str(topic).strip()[:250] + ' explicación español'
    # No cookies, account credentials or transcript text are attached.
    with requests.get('https://www.youtube.com/results', params={'search_query': query},
                      headers={'User-Agent': 'Mozilla/5.0', 'Accept-Language': 'es'},
                      timeout=(10, 25), stream=True) as response:
        response.raise_for_status()
        chunks = []
        size = 0
        for chunk in response.iter_content(65536):
            size += len(chunk)
            if size > 5 * 1024 * 1024:
                raise RuntimeError('La respuesta de YouTube excede el tamaño esperado.')
            chunks.append(chunk)
    found = parse_results(b''.join(chunks).decode('utf-8', errors='replace'))
    return found[0] if found else None
