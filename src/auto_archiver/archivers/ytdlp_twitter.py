# Patched version of TwitterIE to extract metadata of tweets with no videos 
# Removed the `No video could be found in this tweet` error
import re

from yt_dlp.extractor.twitter import TwitterIE, PeriscopeIE, TwitterBroadcastIE, TwitterSpacesIE
from yt_dlp.utils import ( 
    ExtractorError,
    float_or_none,
    format_field,
    int_or_none,
    make_archive_id,
    str_or_none,
    traverse_obj,
    try_get,
    unified_timestamp,
    update_url_query,
    url_or_none,
)

class TwitterIEPatched(TwitterIE):
    def _real_extract(self, url):
        twid, selected_index = self._match_valid_url(url).group('id', 'index')
        status = self._extract_status(twid)

        title = description = traverse_obj(
            status, (('full_text', 'text'), {lambda x: x.replace('\n', ' ')}), get_all=False) or ''
        title = re.sub(r'\s+(https?://[^ ]+)', '', title)
        user = status.get('user') or {}
        uploader = user.get('name')
        if uploader:
            title = f'{uploader} - {title}'
        uploader_id = user.get('screen_name')

        info = {
            'id': twid,
            'title': title,
            'description': description,
            'uploader': uploader,
            'timestamp': unified_timestamp(status.get('created_at')),
            'uploader_id': uploader_id,
            'uploader_url': format_field(uploader_id, None, 'https://twitter.com/%s'),
            'like_count': int_or_none(status.get('favorite_count')),
            'repost_count': int_or_none(status.get('retweet_count')),
            'comment_count': int_or_none(status.get('reply_count')),
            'age_limit': 18 if status.get('possibly_sensitive') else 0,
            'tags': traverse_obj(status, ('entities', 'hashtags', ..., 'text')),
        }

        def extract_from_video_info(media):
            media_id = traverse_obj(media, 'id_str', 'id', expected_type=str_or_none)
            self.write_debug(f'Extracting from video info: {media_id}')

            formats = []
            subtitles = {}
            for variant in traverse_obj(media, ('video_info', 'variants', ...)):
                fmts, subs = self._extract_variant_formats(variant, twid)
                subtitles = self._merge_subtitles(subtitles, subs)
                formats.extend(fmts)

            thumbnails = []
            media_url = media.get('media_url_https') or media.get('media_url')
            if media_url:
                def add_thumbnail(name, size):
                    thumbnails.append({
                        'id': name,
                        'url': update_url_query(media_url, {'name': name}),
                        'width': int_or_none(size.get('w') or size.get('width')),
                        'height': int_or_none(size.get('h') or size.get('height')),
                    })
                for name, size in media.get('sizes', {}).items():
                    add_thumbnail(name, size)
                add_thumbnail('orig', media.get('original_info') or {})

            return {
                'id': media_id,
                'formats': formats,
                'subtitles': subtitles,
                'thumbnails': thumbnails,
                'view_count': traverse_obj(media, ('mediaStats', 'viewCount', {int_or_none})),
                'duration': float_or_none(traverse_obj(media, ('video_info', 'duration_millis')), 1000),
                # The codec of http formats are unknown
                '_format_sort_fields': ('res', 'br', 'size', 'proto'),
            }

        def extract_from_card_info(card):
            if not card:
                return

            self.write_debug(f'Extracting from card info: {card.get("url")}')
            binding_values = card['binding_values']

            def get_binding_value(k):
                o = binding_values.get(k) or {}
                return try_get(o, lambda x: x[x['type'].lower() + '_value'])

            card_name = card['name'].split(':')[-1]
            if card_name == 'player':
                yield {
                    '_type': 'url',
                    'url': get_binding_value('player_url'),
                }
            elif card_name == 'periscope_broadcast':
                yield {
                    '_type': 'url',
                    'url': get_binding_value('url') or get_binding_value('player_url'),
                    'ie_key': PeriscopeIE.ie_key(),
                }
            elif card_name == 'broadcast':
                yield {
                    '_type': 'url',
                    'url': get_binding_value('broadcast_url'),
                    'ie_key': TwitterBroadcastIE.ie_key(),
                }
            elif card_name == 'audiospace':
                yield {
                    '_type': 'url',
                    'url': f'https://twitter.com/i/spaces/{get_binding_value("id")}',
                    'ie_key': TwitterSpacesIE.ie_key(),
                }
            elif card_name == 'summary':
                yield {
                    '_type': 'url',
                    'url': get_binding_value('card_url'),
                }
            elif card_name == 'unified_card':
                unified_card = self._parse_json(get_binding_value('unified_card'), twid)
                yield from map(extract_from_video_info, traverse_obj(
                    unified_card, ('media_entities', ...), expected_type=dict))
            # amplify, promo_video_website, promo_video_convo, appplayer,
            # video_direct_message, poll2choice_video, poll3choice_video,
            # poll4choice_video, ...
            else:
                is_amplify = card_name == 'amplify'
                vmap_url = get_binding_value('amplify_url_vmap') if is_amplify else get_binding_value('player_stream_url')
                content_id = get_binding_value('%s_content_id' % (card_name if is_amplify else 'player'))
                formats, subtitles = self._extract_formats_from_vmap_url(vmap_url, content_id or twid)

                thumbnails = []
                for suffix in ('_small', '', '_large', '_x_large', '_original'):
                    image = get_binding_value('player_image' + suffix) or {}
                    image_url = image.get('url')
                    if not image_url or '/player-placeholder' in image_url:
                        continue
                    thumbnails.append({
                        'id': suffix[1:] if suffix else 'medium',
                        'url': image_url,
                        'width': int_or_none(image.get('width')),
                        'height': int_or_none(image.get('height')),
                    })

                yield {
                    'formats': formats,
                    'subtitles': subtitles,
                    'thumbnails': thumbnails,
                    'duration': int_or_none(get_binding_value(
                        'content_duration_seconds')),
                }

        videos = traverse_obj(status, (
            (None, 'quoted_status'), 'extended_entities', 'media', lambda _, m: m['type'] != 'photo', {dict}))

        if self._yes_playlist(twid, selected_index, video_label='URL-specified video number'):
            selected_entries = (*map(extract_from_video_info, videos), *extract_from_card_info(status.get('card')))
        else:
            desired_obj = traverse_obj(status, (
                (None, 'quoted_status'), 'extended_entities', 'media', int(selected_index) - 1, {dict}), get_all=False)
            if not desired_obj:
                raise ExtractorError(f'Video #{selected_index} is unavailable', expected=True)
            elif desired_obj.get('type') != 'video':
                raise ExtractorError(f'Media #{selected_index} is not a video', expected=True)
            # Restore original archive id and video index in title
            for index, entry in enumerate(videos, 1):
                if entry.get('id') != desired_obj.get('id'):
                    continue
                if index == 1:
                    info['_old_archive_ids'] = [make_archive_id(self, twid)]
                if len(videos) != 1:
                    info['title'] += f' #{index}'
                break

            return {**info, **extract_from_video_info(desired_obj), 'display_id': twid}

        entries = [{**info, **data, 'display_id': twid} for data in selected_entries]
        if not entries:
            expanded_url = traverse_obj(status, ('entities', 'urls', 0, 'expanded_url'), expected_type=url_or_none)
            return self.url_result(expanded_url, display_id=twid, **info)

        entries[0]['_old_archive_ids'] = [make_archive_id(self, twid)]

        if len(entries) == 1:
            return entries[0]

        for index, entry in enumerate(entries, 1):
            entry['title'] += f' #{index}'

        return self.playlist_result(entries, **info)