import re, requests, mimetypes, json
from datetime import datetime
from loguru import logger
from slugify import slugify
from snscrape.modules.twitter import TwitterTweetScraper, Video, Gif, Photo
from twikit import Client
import os

from . import Archiver
from ..core import Metadata, Media
from ..utils import UrlUtil
from .twitter_archiver import TwitterArchiver

class TwitterTwikitArchiver(TwitterArchiver, Archiver):
    """
    This Twitter Archiver uses unofficial scraping methods.
    """

    name = "twitter_twikit_archiver"
    link_pattern = re.compile(r"(?:twitter|x).com\/(?:\#!\/)?(\w+)\/status(?:es)?\/(\d+)")
    link_clean_pattern = re.compile(r"(.+(?:twitter|x)\.com\/.+\/\d+)(\?)*.*")

    def __init__(self, config: dict) -> None:
        super().__init__(config)
        self.assert_valid_string("username")
        self.assert_valid_string("password")
        self.assert_valid_string("email")
        self.client = Client("en-US")
        self.client.login(auth_info_1=self.username, auth_info_2=self.email, password=self.password)

    @staticmethod
    def configs() -> dict:
        return {
            "username": {"default": None, "help": "Twitter username"},
            "password": {"default": None, "help": "Twitter password"},
            "email": {"default": None, "help": "Twitter email"},
        }

    def sanitize_url(self, url: str) -> str:
        # expand URL if t.co and clean tracker GET params
        if 'https://t.co/' in url:
            try:
                r = requests.get(url)
                logger.debug(f'Expanded url {url} to {r.url}')
                url = r.url
            except:
                logger.error(f'Failed to expand url {url}')
        return self.link_clean_pattern.sub("\\1", url)
    
    def download(self, item: Metadata) -> Metadata:
        url = item.get_url()
        _, tweet_id = self.get_username_tweet_id(url)
        result = Metadata()
        tweet = self.client.get_tweet_by_id(tweet_id)
        result.set_content(tweet.text)
        result.set_title(f"{tweet.user.screen_name} - {tweet.text}")
        result.set_timestamp(tweet.created_at_datetime)
        for i, tw_media in enumerate(tweet.media):
            media = Media(filename="")
            mimetype = ""
            if tw_media["type"] == "photo":
                media.set("src", UrlUtil.twitter_best_quality_url(tw_media['media_url_https']))
                mimetype = "image/jpeg"
            elif tw_media["type"] == "video":
                variant = max([v for v in tw_media['video_info']['variants'] if 'bitrate' in v], key=lambda x: x['bitrate'])
                media.set("src", variant['url'])
                mimetype = variant['content_type']
            elif tw_media["type"] == "animated_gif":
                variant = tw_media['video_info']['variants'][0]
                media.set("src", variant['url'])
                mimetype = variant['content_type']
            ext = mimetypes.guess_extension(mimetype)
            media.filename = self.download_from_url(media.get("src"), f'{slugify(url)}_{i}{ext}', item)
            result.add_media(media)
        return result.success("twitter-twikit")
            
