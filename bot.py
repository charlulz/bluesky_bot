from atproto import Client, models
from openai import OpenAI
import os
from dotenv import load_dotenv
import yaml
import glob
from pathlib import Path
import threading
import logging
from datetime import datetime, timedelta, timezone
from colorama import Fore, Style, init
import json
import time
import random
import emoji

init()  # Initialize colorama

def print_success(message):
    """Print a success message in green"""
    print(f"{Fore.GREEN}[SUCCESS] {message}{Style.RESET_ALL}")

def print_error(message):
    """Print an error message in red"""
    print(f"{Fore.RED}[ERROR] {message}{Style.RESET_ALL}")

def print_warning(message):
    """Print a warning message in yellow"""
    print(f"{Fore.YELLOW}[WARNING] {message}{Style.RESET_ALL}")

def print_action(message):
    """Print an action message in blue"""
    print(f"{Fore.BLUE}[ACTION] {message}{Style.RESET_ALL}")


load_dotenv()

class BlueskyBot:
    def __init__(self, config_path):
        """Initialize bot with configuration from yaml file"""
        self.config = self.load_config(config_path)
        self.name = self.config['name']
        self.client = Client()
        self.openai_client = OpenAI()
        
        # Set up bot-specific logging
        self.setup_logging()
        
        # Initialize from yaml config
        self.daily_limits = {
            'follows': 750,
            'likes': 2500,
            'reposts': 450,
            'posts': 100,
            'replies': 7500  # Added replies limit
        }
        # Override with config values if present
        if 'limits' in self.config and 'daily' in self.config['limits']:
            self.daily_limits.update(self.config['limits']['daily'])

        self.search_terms = self.config['engagement']['search_terms']
        self.hashtags = self.config['engagement']['hashtags']
        self.bio_keywords = self.config['engagement']['bio_keywords']
        self.system_prompt = self.config['content']['system_prompt']
        
        # Initialize tracking
        self.followed_users = self.load_followed_users()
        self.engagement_stats = self.load_engagement_stats()
        self.post_history = self.load_post_history()
        
        # Control flags
        self.running = False
        self.paused = False

        # Add follower count tracking
        self.last_follower_count = 0
        self.login()  # Ensure we're logged in
        try:
            self.last_follower_count = self.get_follower_count()
        except Exception as e:
            print_warning(f"[{self.name}] Could not get initial follower count: {e}")

    def load_config(self, config_path):
        """Load and process yaml configuration"""
        with open(config_path, 'r') as file:
            config = yaml.safe_load(file)
            
            # Replace environment variables in credentials
            config['credentials']['username'] = os.getenv(config['credentials']['username'].replace('${', '').replace('}', ''))
            config['credentials']['app_password'] = os.getenv(config['credentials']['app_password'].replace('${', '').replace('}', ''))
            
            return config

    def setup_logging(self):
        """Set up bot-specific logging"""
        log_filename = f"logs/{self.name.lower().replace(' ', '_')}_{datetime.now().strftime('%Y%m%d')}.log"
        os.makedirs('logs', exist_ok=True)
        
        self.logger = logging.getLogger(self.name)
        self.logger.setLevel(logging.INFO)
        
        formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
        
        file_handler = logging.FileHandler(log_filename)
        file_handler.setFormatter(formatter)
        self.logger.addHandler(file_handler)

    def login(self):
        """Login to Bluesky"""
        
        try:
            self.client = Client(base_url="https://bsky.social")
            self.client.login(
                login=self.config['credentials']['username'],
                password=self.config['credentials']['app_password']
            )
            
            self.logger.info(f"Successfully logged in as {self.config['credentials']['username']}")
            print_success(f"[{self.name}] Successfully logged in")
        except Exception as e:
            self.logger.error(f"Login failed: {e}")
            print_error(f"[{self.name}] Login failed: {e}")
            raise

    def load_followed_users(self):
        """Load or create followed users tracking file"""
        filename = f"data/{self.name.lower().replace(' ', '_')}_followed_users.json"
        os.makedirs('data', exist_ok=True)
        
        try:
            with open(filename, 'r') as f:
                return json.load(f)
        except FileNotFoundError:
            # Initialize new followed users tracking
            initial_data = {
                'users': {},  # did: {'handle': handle, 'followed_at': timestamp}
                'blacklist': set(),  # users we don't want to follow again
                'last_reset': str(datetime.now())
            }
            self.save_followed_users(initial_data)
            return initial_data

    def save_followed_users(self, data=None):
        """Save followed users to file"""
        if data is None:
            data = self.followed_users
            
        # Convert set to list for JSON serialization
        if 'blacklist' in data:
            data['blacklist'] = list(data['blacklist'])
            
        filename = f"data/{self.name.lower().replace(' ', '_')}_followed_users.json"
        try:
            with open(filename, 'w') as f:
                json.dump(data, f, indent=2)
        except Exception as e:
            print_error(f"Failed to save followed users: {e}")
            logging.error(f"Error saving followed users: {e}")

    def add_followed_user(self, did, handle):
        """Add a user to our followed list"""
        self.followed_users['users'][did] = {
            'handle': handle,
            'followed_at': str(datetime.now())
        }
        self.save_followed_users()

    def blacklist_user(self, did):
        """Add a user to our blacklist"""
        if isinstance(self.followed_users['blacklist'], list):
            self.followed_users['blacklist'] = set(self.followed_users['blacklist'])
        self.followed_users['blacklist'].add(did)
        if did in self.followed_users['users']:
            del self.followed_users['users'][did]
        self.save_followed_users()

    def remove_followed_user(self, did):
        """Remove a user from our followed list"""
        if did in self.followed_users['users']:
            del self.followed_users['users'][did]
            self.save_followed_users()

    def load_engagement_stats(self):
        """Load or create engagement stats tracking file"""
        filename = f"data/{self.name.lower().replace(' ', '_')}_engagement_stats.json"
        os.makedirs('data', exist_ok=True)
        
        try:
            with open(filename, 'r') as f:
                stats = json.load(f)
                
                # Reset stats if it's a new day
                last_reset = datetime.fromisoformat(stats.get('last_reset', '2000-01-01'))
                if datetime.now().date() > last_reset.date():
                    stats = self.reset_engagement_stats()
                return stats['counts']
                
        except FileNotFoundError:
            return self.reset_engagement_stats()

    def reset_engagement_stats(self):
        """Reset engagement stats for a new day"""
        stats = {
            'last_reset': str(datetime.now()),
            'counts': {
                'follows': 0,
                'likes': 0,
                'reposts': 0,
                'posts': 0,
                'replies': 0  # Added replies tracking
            }
        }
        self.save_engagement_stats(stats['counts'])
        return stats['counts']

    def save_engagement_stats(self, stats=None):
        """Save current engagement stats to file"""
        try:
            if stats is None:
                stats = self.engagement_stats
                
            data = {
                'last_reset': str(datetime.now()),
                'counts': stats
            }
            
            filename = f"data/{self.name.lower().replace(' ', '_')}_engagement_stats.json"
            with open(filename, 'w') as f:
                json.dump(data, f, indent=2)
                
        except Exception as e:
            print_error(f"Failed to save engagement stats: {e}")
            logging.error(f"Error saving engagement stats: {e}")

    def increment_stat(self, stat_name):
        """Safely increment a stat and save it"""
        try:
            if stat_name in self.engagement_stats:
                self.engagement_stats[stat_name] += 1
                self.save_engagement_stats()
                return True
            return False
        except Exception as e:
            logging.error(f"Error incrementing stat {stat_name}: {e}")
            return False

    def can_perform_action(self, action_type):
        """Check if we can perform an action based on daily limits"""
        return self.engagement_stats.get(action_type, 0) < self.daily_limits.get(action_type, 0)

    def load_post_history(self):
        """Load or create post history tracking file"""
        filename = f"data/{self.name.lower().replace(' ', '_')}_post_history.json"
        os.makedirs('data', exist_ok=True)
        
        try:
            with open(filename, 'r') as f:
                history = json.load(f)
                
                # Clean up old entries (older than 7 days)
                current_time = datetime.now()
                history['posts'] = {
                    uri: data for uri, data in history['posts'].items()
                    if (current_time - datetime.fromisoformat(data['timestamp'])).days <= 7
                }
                
                self.save_post_history(history)
                return history
                
        except FileNotFoundError:
            # Initialize new post history
            initial_data = {
                'posts': {},  # uri: {'text': text, 'timestamp': timestamp}
                'last_post': None
            }
            self.save_post_history(initial_data)
            return initial_data

    def save_post_history(self, history=None):
        """Save post history to file"""
        try:
            if history is None:
                history = self.post_history
                
            filename = f"data/{self.name.lower().replace(' ', '_')}_post_history.json"
            with open(filename, 'w') as f:
                json.dump(history, f, indent=2)
                
        except Exception as e:
            print_error(f"Failed to save post history: {e}")
            logging.error(f"Error saving post history: {e}")

    def add_post_to_history(self, uri, text):
        """Add a post to our history"""
        self.post_history['posts'][uri] = {
            'text': text,
            'timestamp': str(datetime.now())
        }
        self.post_history['last_post'] = str(datetime.now())
        self.save_post_history()

    def has_posted_recently(self, minutes=15):
        """Check if we've posted within the last X minutes"""
        if not self.post_history['last_post']:
            return False
            
        last_post_time = datetime.fromisoformat(self.post_history['last_post'])
        time_since_post = datetime.now() - last_post_time
        return time_since_post.total_seconds() < (minutes * 60)

    def has_replied_to_post(self, uri):
        """Check if we've already replied to a post"""
        return uri in self.post_history['posts']

    def find_new_users_to_follow(self, limit=50):
        """Find new users to follow using multiple strategies"""
        try:
            print_action(f"[{self.name}] Finding new users to follow...")
            potential_users = set()
            
            # Get current time for filtering
            current_time = datetime.now().strftime("%Y-%m-%dT%H:%M:%SZ")
            week_ago = (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%dT%H:%M:%SZ")
            
            # Get trending hashtags
            trending_tags = self.get_trending_hashtags()
            search_terms = list(set(self.search_terms + trending_tags))
            
            # Search by terms
            for term in random.sample(search_terms, min(5, len(search_terms))):
                print_action(f"[{self.name}] Searching users with term: {term}")
                try:
                    search_results = self.client.app.bsky.feed.search_posts({
                        'q': term,
                        'limit': 25,
                        'sort': 'latest'  # Focus on recent posts
                    })
                    
                    if hasattr(search_results, 'posts'):
                        for post in search_results.posts:
                            if hasattr(post, 'author'):
                                # Check if user has posted recently
                                if self.is_recently_active_user(post.author.did):
                                    potential_users.add((post.author.did, post.author.handle))
                                
                except Exception as e:
                    print_warning(f"[{self.name}] Search failed for term {term}: {e}")
                    continue
                
                time.sleep(random.uniform(1, 2))

            # Filter and return results
            filtered_users = self.filter_users_by_engagement(potential_users)
            
            print_success(f"[{self.name}] Found {len(filtered_users)} new users to follow")
            return filtered_users[:limit]
            
        except Exception as e:
            print_error(f"[{self.name}] Failed to find new users: {e}")
            self.logger.error(f"Error finding new users: {e}")
            return []

    def get_trending_hashtags(self):
        """Get trending hashtags using OpenAI"""
        try:
            response = self.openai_client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[
                    {
                        "role": "system",
                        "content": "You are a social media expert. Generate relevant hashtags."
                    },
                    {
                        "role": "user",
                        "content": f"Generate 5 trending hashtags related to these topics: {', '.join(self.search_terms)}"
                    }
                ],
                max_tokens=50,
                temperature=0.7
            )
            
            hashtags = response.choices[0].message.content.strip().split()
            return [tag.strip('#') for tag in hashtags if tag.startswith('#')]
            
        except Exception as e:
            print_warning(f"[{self.name}] Error getting trending hashtags: {e}")
            return []

    def is_recently_active_user(self, user_did):
        """Check if user has posted in the last few days"""
        try:
            profile = self.client.app.bsky.actor.get_profile({'actor': user_did})
            feed = self.client.app.bsky.feed.get_author_feed({'actor': user_did, 'limit': 1})
            
            if hasattr(feed, 'feed') and feed.feed:
                latest_post = feed.feed[0]
                post_time = datetime.fromisoformat(latest_post.post.indexed_at.replace('Z', '+00:00'))
                days_since_post = (datetime.now(timezone.utc) - post_time).days
                return days_since_post <= 3  # Active in last 3 days
                
            return False
            
        except Exception as e:
            print_warning(f"[{self.name}] Error checking user activity: {e}")
            return False

    def filter_users_by_engagement(self, users):
        """Filter users based on their engagement metrics"""
        filtered_users = []
        for user_did, handle in users:
            try:
                profile = self.client.app.bsky.actor.get_profile({'actor': user_did})
                
                # Calculate engagement metrics
                follower_ratio = profile.followers_count / profile.follows_count if profile.follows_count > 0 else 0
                posts_per_day = profile.posts_count / 30  # Rough estimate
                
                # Score the user
                score = (follower_ratio * 0.5) + (posts_per_day * 0.5)
                
                if score > 1.0:  # Adjust threshold as needed
                    filtered_users.append((user_did, handle))
                    
            except Exception as e:
                print_warning(f"[{self.name}] Error filtering user {handle}: {e}")
                continue
                
            time.sleep(random.uniform(0.5, 1))
            
        return filtered_users

    def follow_user(self, did, handle):
        """Follow a user and track the action"""
        try:
            self.client.follow(did)
            self.add_followed_user(did, handle)
            self.increment_stat('follows')
            print_success(f"[{self.name}] Followed user: {handle}")
            self.logger.info(f"Followed user: {handle}")
            return True
        except Exception as e:
            print_error(f"[{self.name}] Failed to follow user {handle}: {e}")
            self.logger.error(f"Failed to follow user {handle}: {e}")
            return False

    def should_follow_user(self, author):
        """Determine if we should follow a user based on their profile"""
        try:
            if not hasattr(author, 'description'):
                return False
                
            # Check if any of our keywords appear in their bio
            bio_text = author.description.lower()
            if any(keyword.lower() in bio_text for keyword in self.bio_keywords):
                return True
                
            return False
            
        except Exception as e:
            self.logger.error(f"Error checking user follow criteria: {e}")
            return False

    def run(self):
        """Main bot loop"""
        try:
            self.running = True
            last_analysis = datetime.now()
            print_success(f"[{self.name}] Bot started successfully")
            self.logger.info("Bot started successfully")

            while self.running:
                try:
                    if not self.paused:
                        # Track follower count
                        self.track_follower_count()

                        # Run growth analysis every 4 hours
                        if (datetime.now() - last_analysis).total_seconds() > 14400:  # 4 hours
                            self.analyze_growth_rate()
                            self.analyze_engagement_effectiveness()
                            last_analysis = datetime.now()

                        # Print current engagement stats
                        print_action(f"[{self.name}] Current engagement stats:")
                        for action, count in self.engagement_stats.items():
                            print_action(f"- {action}: {count}/{self.daily_limits.get(action, 'unlimited')}")

                        # Find and engage with posts (likes, reposts, replies)
                        if any(self.can_perform_action(action) for action in ['likes', 'reposts', 'replies']):
                            print_action(f"[{self.name}] Looking for posts to engage with...")
                            posts = self.find_posts_to_comment(limit=1000)
                            print_action(f"[{self.name}] Found {len(posts)} posts to potentially engage with")
                            
                            for post_data in posts:
                                # Like posts
                                if self.can_perform_action('likes'):
                                    try:
                                        print_action(f"[{self.name}] Attempting to like post by {post_data['author'].handle}")
                                        self.client.like(post_data['uri'], post_data['cid'])
                                        self.increment_stat('likes')
                                        self.track_engagement_result('likes')
                                        print_success(f"[{self.name}] Liked post by {post_data['author'].handle}")
                                        time.sleep(random.uniform(2, 5))
                                    except Exception as e:
                                        print_error(f"[{self.name}] Failed to like post: {e}")

                                # Repost some posts
                                if self.can_perform_action('reposts') and random.random() < 0.3:
                                    try:
                                        print_action(f"[{self.name}] Attempting to repost by {post_data['author'].handle}")
                                        self.client.repost(post_data['uri'], post_data['cid'])
                                        self.increment_stat('reposts')
                                        self.track_engagement_result('reposts')
                                        print_success(f"[{self.name}] Reposted post by {post_data['author'].handle}")
                                        time.sleep(random.uniform(2, 5))
                                    except Exception as e:
                                        print_error(f"[{self.name}] Failed to repost: {e}")

                                # Reply to some posts
                                if self.can_perform_action('replies') and not self.has_replied_to_post(post_data['uri']):
                                    if random.random() < 0.4:
                                        print_action(f"[{self.name}] Attempting to reply to {post_data['author'].handle}")
                                        self.create_engaging_reply(post_data['post'])
                                        self.track_engagement_result('replies')
                                        time.sleep(random.uniform(5, 10))

                        # Find and follow new users
                        if self.can_perform_action('follows'):
                            new_users = self.find_new_users_to_follow(limit=300)
                            for user_did, handle in new_users:
                                if self.follow_user(user_did, handle):
                                    self.track_engagement_result('follows')
                                    time.sleep(random.uniform(30, 60))

                        # Sleep between cycles
                        sleep_time = random.uniform(180, 300)  # 3-5 minutes
                        print_action(f"[{self.name}] Sleeping for {int(sleep_time/60)} minutes...")
                        time.sleep(sleep_time)

                except Exception as e:
                    self.logger.error(f"Error in main loop: {e}")
                    print_error(f"[{self.name}] Error in main loop: {e}")
                    time.sleep(300)  # Sleep for 5 minutes on error

        except KeyboardInterrupt:
            print_action(f"[{self.name}] Shutting down gracefully...")
            self.running = False
        except Exception as e:
            self.logger.error(f"Fatal error: {e}")
            print_error(f"[{self.name}] Fatal error: {e}")
            self.running = False

    def find_posts_to_comment(self, limit=20):
        """Find posts worth engaging with"""
        try:
            print_action(f"[{self.name}] Searching for posts to comment on...")
            relevant_posts = []
            
            # Search by terms from config
            for search_term in random.sample(self.search_terms, min(3, len(self.search_terms))):
                try:
                    print_action(f"[{self.name}] Searching posts with term: {search_term}")
                    search_results = self.client.app.bsky.feed.search_posts({
                        'q': search_term,
                        'limit': 100,
                        'sort': 'latest'  # Changed to latest to get more results
                    })
                    
                    # Debug logging
                    if hasattr(search_results, 'posts'):
                        print_action(f"[{self.name}] Found {len(search_results.posts)} posts for term: {search_term}")
                    else:
                        print_warning(f"[{self.name}] No 'posts' attribute in search results for term: {search_term}")
                        continue
                    
                    if hasattr(search_results, 'posts'):
                        for post in search_results.posts:
                            try:
                                if self.is_worth_commenting(post):
                                    # Store the entire post object
                                    relevant_posts.append({
                                        'post': post,
                                        'uri': post.uri,
                                        'cid': post.cid,
                                        'text': post.record.text,
                                        'author': post.author
                                    })
                                    print_action(f"[{self.name}] Added relevant post: '{post.record.text[:50]}...'")
                            except Exception as e:
                                print_warning(f"[{self.name}] Error processing post: {e}")
                                continue
                                
                except Exception as e:
                    print_warning(f"[{self.name}] Search failed for term {search_term}: {e}")
                    continue
                    
                time.sleep(random.uniform(1, 2))

            # Shuffle and limit results
            random.shuffle(relevant_posts)
            results = relevant_posts[:limit]
            
            print_action(f"[{self.name}] Found {len(results)} posts to engage with")
            return results
            
        except Exception as e:
            print_error(f"[{self.name}] Failed to find posts: {e}")
            self.logger.error(f"Error finding posts: {e}")
            return []

    def is_worth_commenting(self, post):
        """Determine if a post is worth engaging with based on user influence"""
        try:
            # Debug logging
            print_action(f"[{self.name}] Evaluating post...")
            
            # Basic validation
            if not hasattr(post, 'record') or not hasattr(post.record, 'text'):
                print_warning(f"[{self.name}] Post has no text content")
                return False
                
            if not post.record.text:
                print_warning(f"[{self.name}] Post text is empty")
                return False
                
            # Don't engage with our own posts
            if post.author.did == self.client.me.did:
                print_warning(f"[{self.name}] Post is from self")
                return False
                
            # Don't engage if we've already replied
            if self.has_replied_to_post(post.uri):
                print_warning(f"[{self.name}] Already replied to post")
                return False

            # Check user influence
            try:
                profile = self.client.app.bsky.actor.get_profile({'actor': post.author.did})
                follower_count = profile.followers_count
                following_count = profile.follows_count
                
                # Calculate engagement ratio
                engagement_ratio = follower_count / following_count if following_count > 0 else 0
                
                # Prioritize users with good engagement ratios
                if engagement_ratio > 1.5:  # They have 50% more followers than following
                    print_success(f"[{self.name}] High-influence user found: {post.author.handle}")
                    return True
                    
                # Also engage with active users
                if hasattr(profile, 'posts_count') and profile.posts_count > 100:
                    print_success(f"[{self.name}] Active user found: {post.author.handle}")
                    return True
                    
            except Exception as e:
                print_warning(f"[{self.name}] Error checking user influence: {e}")
                
            # Default to basic engagement criteria
            return True
            
        except Exception as e:
            print_warning(f"[{self.name}] Error evaluating post: {e}")
            self.logger.error(f"Error evaluating post: {e}")
            return False

    def create_engaging_reply(self, post):
        """Create a natural, casual reply using OpenAI with custom personality"""
        try:
            # Build context about the post and author
            context = self.build_post_context(post)
            
            # Get custom system prompt from config, or use default
            system_prompt = self.config.get('engagement_style', {}).get('system_prompt', """
                You're a casual social media user. Keep responses natural and friendly.
                Use casual language and don't sound like a bot.
                Keep it under 200 chars.
            """)
            
            # Get custom temperature from config, or use default
            temperature = self.config.get('engagement_style', {}).get('temperature', 0.9)

            response = self.openai_client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[
                    {
                        "role": "system",
                        "content": f"{system_prompt}\nMatch their vibe: {context['writing_style']}"
                    },
                    {
                        "role": "user",
                        "content": f"Reply to this in your style: {post.record.text}"
                    }
                ],
                max_tokens=100,
                temperature=temperature
            )
            
            reply_text = response.choices[0].message.content.strip()
            
            # Ensure reply isn't too long
            if len(reply_text) > 280:
                reply_text = reply_text[:277] + "..."
            
            # Optional: Enforce emoji limit if specified
            max_emojis = self.config.get('engagement_style', {}).get('max_emojis')
            if max_emojis:
                reply_text = self.limit_emojis(reply_text, max_emojis)
            
            # Create the reply
            result = self.client.send_post(
                text=reply_text,
                reply_to={"root": {"uri": post.uri, "cid": post.cid}, 
                         "parent": {"uri": post.uri, "cid": post.cid}}
            )
            
            if result:
                self.add_post_to_history(post.uri, reply_text)
                self.increment_stat('replies')
                print_success(f"[{self.name}] Created reply: {reply_text[:50]}...")
            
        except Exception as e:
            print_error(f"[{self.name}] Error creating reply: {e}")
            self.logger.error(f"Error creating reply: {e}")

    def limit_emojis(self, text, max_emojis):
        """Limit the number of emojis in the text"""
        # Convert text to list of characters
        chars = list(text)
        emoji_count = 0
        result = []
        
        for char in chars:
            if emoji.is_emoji(char):
                if emoji_count < max_emojis:
                    result.append(char)
                    emoji_count += 1
            else:
                result.append(char)
                
        return ''.join(result)

    def build_post_context(self, post):
        """Analyze post and author to build context for personalization"""
        try:
            context = {
                'author_interests': [],
                'writing_style': 'casual',
                'tone': 'friendly',
                'value_add': 'insights'
            }
            
            # Analyze author's profile and recent posts
            profile = self.client.app.bsky.actor.get_profile({'actor': post.author.did})
            recent_posts = self.get_recent_posts(post.author.did)
            
            # Extract interests from bio
            if hasattr(profile, 'description'):
                context['author_interests'] = self.extract_interests(profile.description)
            
            # Analyze writing style from recent posts
            if recent_posts:
                context.update(self.analyze_writing_style(recent_posts))
            
            # Determine best value-add approach
            context['value_add'] = self.determine_value_add(post, profile)
            
            return context
            
        except Exception as e:
            print_warning(f"[{self.name}] Error building context: {e}")
            return {
                'author_interests': self.search_terms,
                'writing_style': 'casual',
                'tone': 'friendly',
                'value_add': 'insights'
            }

    def get_recent_posts(self, author_did, limit=5):
        """Get author's recent posts for analysis"""
        try:
            feed = self.client.app.bsky.feed.get_author_feed({
                'actor': author_did,
                'limit': limit
            })
            
            if hasattr(feed, 'feed'):
                return [post.post.record.text for post in feed.feed 
                       if hasattr(post.post, 'record') and hasattr(post.post.record, 'text')]
            return []
            
        except Exception as e:
            print_warning(f"[{self.name}] Error getting recent posts: {e}")
            return []

    def extract_interests(self, bio):
        """Extract interests from user's bio using OpenAI"""
        try:
            # Check if bio is empty or None
            if not bio or not isinstance(bio, str):
                print_warning(f"[{self.name}] Empty or invalid bio, using default interests")
                return self.search_terms
                
            response = self.openai_client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[
                    {
                        "role": "system",
                        "content": "Extract key interests and topics from this bio. Return as comma-separated list."
                    },
                    {
                        "role": "user",
                        "content": bio
                    }
                ],
                max_tokens=50,
                temperature=0.5
            )
            
            interests = response.choices[0].message.content.strip().split(',')
            return [interest.strip() for interest in interests]
            
        except Exception as e:
            print_warning(f"[{self.name}] Error extracting interests: {e}")
            return self.search_terms

    def analyze_writing_style(self, posts):
        """Analyze writing style from recent posts using OpenAI"""
        try:
            # Check if posts list is empty
            if not posts:
                return {'writing_style': 'casual'}
                
            posts_text = "\n".join(filter(None, posts))  # Filter out None/empty posts
            
            # Check if we have any text to analyze
            if not posts_text.strip():
                return {'writing_style': 'casual'}
                
            response = self.openai_client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[
                    {
                        "role": "system",
                        "content": """You are a writing style analyzer. 
                        Return ONLY a JSON object with a single key 'writing_style' and one of these values:
                        'casual', 'formal', 'friendly', 'professional', or 'enthusiastic'.
                        Example: {"writing_style": "casual"}"""
                    },
                    {
                        "role": "user",
                        "content": f"Analyze this writing style: {posts_text[:500]}"  # Limit text length
                    }
                ],
                max_tokens=50,
                temperature=0.3  # Lower temperature for more consistent output
            )
            
            try:
                # Get the response content
                content = response.choices[0].message.content.strip()
                
                # If response doesn't start with {, assume it's not JSON
                if not content.startswith('{'):
                    print_warning(f"[{self.name}] Invalid JSON response: {content}")
                    return {'writing_style': 'casual'}
                
                # Parse JSON response
                analysis = json.loads(content)
                
                # Validate the response format
                if 'writing_style' not in analysis:
                    print_warning(f"[{self.name}] Missing writing_style in response")
                    return {'writing_style': 'casual'}
                    
                # Ensure writing_style is one of our expected values
                valid_styles = {'casual', 'formal', 'friendly', 'professional', 'enthusiastic'}
                if analysis['writing_style'] not in valid_styles:
                    print_warning(f"[{self.name}] Invalid writing style: {analysis['writing_style']}")
                    return {'writing_style': 'casual'}
                
                return analysis
                
            except json.JSONDecodeError as e:
                print_warning(f"[{self.name}] JSON parsing error: {str(e)}")
                return {'writing_style': 'casual'}
            
        except Exception as e:
            print_warning(f"[{self.name}] Error analyzing writing style: {str(e)}")
            return {'writing_style': 'casual'}

    def determine_value_add(self, post, profile):
        """Determine best way to add value based on post and profile"""
        try:
            follower_count = profile.followers_count
            
            # For influencers, focus on meaningful discussion
            if follower_count > 10000:
                return 'thoughtful discussion'
            
            # For technical posts, provide insights
            if any(term in post.record.text.lower() for term in ['code', 'programming', 'tech']):
                return 'technical insights'
            
            # For questions, provide helpful answers
            if '?' in post.record.text:
                return 'helpful answers'
            
            # Default to general insights
            return 'insights'
            
        except Exception as e:
            print_warning(f"[{self.name}] Error determining value add: {e}")
            return 'insights'

    def create_original_post(self):
        """Create an engaging original post using OpenAI"""
        try:
            if self.has_posted_recently(minutes=30):
                return
                
            # Randomly select post type with weights
            post_type = random.choices(
                ['question', 'tip', 'discussion', 'trend'],
                weights=[0.3, 0.3, 0.2, 0.2]
            )[0]
            
            # Create prompt based on post type
            prompt = self.get_post_prompt(post_type)
            
            response = self.openai_client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[
                    {
                        "role": "system",
                        "content": f"{self.system_prompt}\nCreate engaging content that encourages interaction."
                    },
                    {
                        "role": "user",
                        "content": prompt
                    }
                ],
                max_tokens=150,
                temperature=0.8
            )
            
            post_text = response.choices[0].message.content.strip()
            
            # Check if it's a good time to post
            if not self.is_good_posting_time():
                print_action(f"[{self.name}] Waiting for better posting time...")
                return
                
            # Post the content
            result = self.client.post(text=post_text)
            self.add_post_to_history(result.uri, post_text)
            self.increment_stat('posts')
            
            print_success(f"[{self.name}] Created {post_type} post: {post_text[:50]}...")
            self.logger.info(f"Created {post_type} post: {post_text[:50]}...")
            
        except Exception as e:
            print_error(f"[{self.name}] Error creating post: {e}")
            self.logger.error(f"Error creating post: {e}")

    def get_post_prompt(self, post_type):
        """Generate appropriate prompt based on post type"""
        topic = random.choice(self.search_terms)
        hashtags = random.sample(self.hashtags, min(3, len(self.hashtags)))
        
        prompts = {
            'question': f"Create an engaging question about {topic} that encourages discussion. "
                       f"Include these hashtags where relevant: {', '.join(hashtags)}",
            
            'tip': f"Share a helpful tip or insight about {topic}. "
                  f"Make it actionable and include these hashtags where relevant: {', '.join(hashtags)}",
            
            'discussion': f"Start a discussion about {topic} with a thought-provoking statement. "
                         f"Include these hashtags where relevant: {', '.join(hashtags)}",
            
            'trend': f"Share an interesting trend or development in {topic}. "
                    f"Include these hashtags where relevant: {', '.join(hashtags)}"
        }
        
        return prompts.get(post_type, prompts['discussion'])

    def is_good_posting_time(self):
        """Check if current time is optimal for posting"""
        try:
            current_hour = datetime.now().hour
            
            # Define peak hours (adjust based on your audience)
            peak_hours = {
                'weekday': [(7, 9), (12, 14), (17, 22)],  # Morning, Lunch, Evening
                'weekend': [(9, 22)]  # More relaxed on weekends
            }
            
            is_weekend = datetime.now().weekday() >= 5
            current_peaks = peak_hours['weekend'] if is_weekend else peak_hours['weekday']
            
            # Check if current hour falls within peak times
            for start, end in current_peaks:
                if start <= current_hour < end:
                    return True
                    
            # 20% chance to post anyway during off-peak hours
            return random.random() < 0.2
            
        except Exception as e:
            self.logger.error(f"Error checking posting time: {e}")
            return True  # Default to allowing posts if check fails

    def track_follower_count(self):
        """Track follower count over time"""
        try:
            filename = f"data/{self.name.lower().replace(' ', '_')}_follower_stats.json"
            os.makedirs('data', exist_ok=True)
            
            # Load existing stats
            try:
                with open(filename, 'r') as f:
                    stats = json.load(f)
            except FileNotFoundError:
                stats = {
                    'snapshots': [],
                    'last_check': None
                }
            
            # Check if 30 minutes have passed since last check
            current_time = datetime.now()
            if stats['last_check']:
                last_check = datetime.fromisoformat(stats['last_check'])
                if (current_time - last_check).total_seconds() < 1800:  # 1800 seconds = 30 minutes
                    return
            
            # Get current follower count
            profile = self.client.app.bsky.actor.get_profile({'actor': self.client.me.did})
            follower_count = profile.followers_count
            
            # Add new snapshot
            stats['snapshots'].append({
                'timestamp': str(current_time),
                'follower_count': follower_count,
                'following_count': profile.follows_count,
                'post_count': profile.posts_count
            })
            
            stats['last_check'] = str(current_time)
            
            # Save updated stats
            with open(filename, 'w') as f:
                json.dump(stats, f, indent=2)
                
            print_action(f"[{self.name}] Recorded follower count: {follower_count}")
            self.logger.info(f"Recorded follower count: {follower_count}")
            
        except Exception as e:
            print_error(f"[{self.name}] Failed to track follower count: {e}")
            self.logger.error(f"Error tracking follower count: {e}")

    def analyze_growth_rate(self):
        """Analyze follower growth rate and provide insights"""
        try:
            filename = f"data/{self.name.lower().replace(' ', '_')}_follower_stats.json"
            
            try:
                with open(filename, 'r') as f:
                    stats = json.load(f)
            except FileNotFoundError:
                print_warning(f"[{self.name}] No follower stats found yet")
                return
                
            snapshots = stats.get('snapshots', [])
            if len(snapshots) < 2:
                print_warning(f"[{self.name}] Not enough data for growth analysis yet")
                return
                
            # Sort snapshots by timestamp
            snapshots.sort(key=lambda x: x['timestamp'])
            
            # Calculate various metrics
            total_growth = snapshots[-1]['follower_count'] - snapshots[0]['follower_count']
            time_diff = (datetime.fromisoformat(snapshots[-1]['timestamp']) - 
                        datetime.fromisoformat(snapshots[0]['timestamp']))
            hours_diff = time_diff.total_seconds() / 3600
            
            # Calculate growth rates
            hourly_rate = total_growth / hours_diff if hours_diff > 0 else 0
            daily_rate = hourly_rate * 24
            weekly_rate = daily_rate * 7
            
            # Calculate engagement ratio
            latest = snapshots[-1]
            engagement_ratio = latest['follower_count'] / latest['following_count'] if latest['following_count'] > 0 else 0
            
            # Get 24-hour growth if we have enough data
            day_ago = datetime.now() - timedelta(days=1)
            day_snapshots = [s for s in snapshots if datetime.fromisoformat(s['timestamp']) > day_ago]
            if day_snapshots:
                day_growth = day_snapshots[-1]['follower_count'] - day_snapshots[0]['follower_count']
            else:
                day_growth = "Insufficient data"
            
            # Print analysis
            print_action(f"\n[{self.name}] Growth Rate Analysis:")
            print(f"""
📊 Current Stats:
• Followers: {latest['follower_count']}
• Following: {latest['following_count']}
• Posts: {latest['post_count']}

📈 Growth Metrics:
• Total Growth: {total_growth} followers
• Past 24 Hours: {day_growth} followers
• Average Growth Rate:
  - {hourly_rate:.2f} followers/hour
  - {daily_rate:.2f} followers/day
  - {weekly_rate:.2f} followers/week

🎯 Engagement:
• Follower/Following Ratio: {engagement_ratio:.2f}
• Posts per Follower: {latest['post_count']/latest['follower_count']:.3f}

⏰ Tracking Period:
• Start: {snapshots[0]['timestamp']}
• Latest: {snapshots[-1]['timestamp']}
• Duration: {time_diff.days} days, {time_diff.seconds//3600} hours
            """)
            
            # Log the analysis
            self.logger.info(f"Growth analysis - Total growth: {total_growth}, Daily rate: {daily_rate:.2f}")
            
            return {
                'total_growth': total_growth,
                'hourly_rate': hourly_rate,
                'daily_rate': daily_rate,
                'weekly_rate': weekly_rate,
                'engagement_ratio': engagement_ratio,
                'tracking_days': time_diff.days
            }
            
        except Exception as e:
            print_error(f"[{self.name}] Failed to analyze growth rate: {e}")
            self.logger.error(f"Error analyzing growth rate: {e}")
            return None

    def analyze_engagement_effectiveness(self):
        """Analyze which engagement actions are most effective"""
        try:
            # Load historical data
            history = self.load_engagement_history()
            current_time = datetime.now()
            
            # Calculate effectiveness for each action type
            effectiveness = {
                'follows': self.calculate_action_effectiveness('follows', history),
                'likes': self.calculate_action_effectiveness('likes', history),
                'replies': self.calculate_action_effectiveness('replies', history),
                'reposts': self.calculate_action_effectiveness('reposts', history)
            }
            
            # Adjust daily limits based on effectiveness
            self.adjust_engagement_limits(effectiveness)
            
            # Log the analysis
            self.logger.info(f"Engagement effectiveness: {effectiveness}")
            print_action(f"[{self.name}] Updated engagement strategy based on effectiveness")
            
        except Exception as e:
            print_error(f"[{self.name}] Error analyzing engagement: {e}")
            self.logger.error(f"Error analyzing engagement: {e}")

    def calculate_action_effectiveness(self, action_type, history):
        """Calculate the effectiveness score for a specific action type"""
        try:
            if not history.get(action_type):
                return 1.0  # Default effectiveness
                
            total_actions = sum(entry['count'] for entry in history[action_type])
            total_followers_gained = sum(entry['followers_gained'] for entry in history[action_type])
            
            if total_actions == 0:
                return 1.0
                
            return (total_followers_gained / total_actions) * 100
            
        except Exception as e:
            self.logger.error(f"Error calculating {action_type} effectiveness: {e}")
            return 1.0

    def adjust_engagement_limits(self, effectiveness):
        """Dynamically adjust daily limits based on effectiveness"""
        try:
            total_effectiveness = sum(effectiveness.values())
            if total_effectiveness == 0:
                return
                
            # Calculate new limits while maintaining total daily actions
            total_actions = sum(self.daily_limits.values())
            new_limits = {}
            
            for action_type, score in effectiveness.items():
                # Calculate new limit based on effectiveness
                new_limit = int((score / total_effectiveness) * total_actions)
                
                # Ensure minimum and maximum thresholds
                min_limit = int(self.daily_limits[action_type] * 0.2)  # 20% of original
                max_limit = int(self.daily_limits[action_type] * 1.5)  # 150% of original
                
                new_limits[action_type] = max(min_limit, min(new_limit, max_limit))
            
            # Update daily limits
            self.daily_limits.update(new_limits)
            self.save_engagement_config()
            
            print_success(f"[{self.name}] Updated daily limits: {new_limits}")
            
        except Exception as e:
            print_error(f"[{self.name}] Error adjusting limits: {e}")
            self.logger.error(f"Error adjusting limits: {e}")

    def track_engagement_result(self, action_type):
        """Track the result of an engagement action"""
        try:
            current_followers = self.get_follower_count()
            
            # Load current tracking period
            history = self.load_engagement_history()
            current_period = datetime.now().strftime('%Y-%m-%d-%H')
            
            if current_period not in history[action_type]:
                history[action_type][current_period] = {
                    'count': 0,
                    'followers_gained': 0,
                    'timestamp': str(datetime.now())
                }
            
            # Update counts
            history[action_type][current_period]['count'] += 1
            followers_gained = current_followers - self.last_follower_count
            history[action_type][current_period]['followers_gained'] += max(0, followers_gained)
            
            # Save updated history
            self.save_engagement_history(history)
            self.last_follower_count = current_followers
            
        except Exception as e:
            print_error(f"[{self.name}] Error tracking engagement: {e}")
            self.logger.error(f"Error tracking engagement: {e}")

    def load_engagement_history(self):
        """Load or create engagement history tracking file"""
        filename = f"data/{self.name.lower().replace(' ', '_')}_engagement_history.json"
        os.makedirs('data', exist_ok=True)
        
        try:
            with open(filename, 'r') as f:
                history = json.load(f)
                
                # Clean up old entries (older than 7 days)
                current_time = datetime.now()
                for action_type in history:
                    history[action_type] = {
                        period: data for period, data in history[action_type].items()
                        if (current_time - datetime.fromisoformat(data['timestamp'])).days <= 7
                    }
                
                return history
                
        except FileNotFoundError:
            # Initialize new history tracking
            return {
                'follows': {},
                'likes': {},
                'replies': {},
                'reposts': {}
            }

    def save_engagement_history(self, history):
        """Save engagement history to file"""
        try:
            filename = f"data/{self.name.lower().replace(' ', '_')}_engagement_history.json"
            with open(filename, 'w') as f:
                json.dump(history, f, indent=2)
                
        except Exception as e:
            print_error(f"[{self.name}] Failed to save engagement history: {e}")
            self.logger.error(f"Error saving engagement history: {e}")

    def save_engagement_config(self):
        """Save current engagement configuration"""
        try:
            filename = f"data/{self.name.lower().replace(' ', '_')}_engagement_config.json"
            config = {
                'daily_limits': self.daily_limits,
                'last_updated': str(datetime.now())
            }
            
            with open(filename, 'w') as f:
                json.dump(config, f, indent=2)
                
        except Exception as e:
            print_error(f"[{self.name}] Failed to save engagement config: {e}")
            self.logger.error(f"Error saving engagement config: {e}")

    def get_follower_count(self):
        """Get current follower count for the bot"""
        try:
            # Get profile using credentials from config
            profile = self.client.app.bsky.actor.get_profile({
                'actor': self.config['credentials']['username']
            })
            
            # Store follower count in instance variable
            self.last_follower_count = getattr(profile, 'followers_count', 0)
            return self.last_follower_count
            
        except Exception as e:
            print_error(f"[{self.name}] Error getting follower count: {e}")
            self.logger.error(f"Error getting follower count: {e}")
            return self.last_follower_count if hasattr(self, 'last_follower_count') else 0

def run_bot(config_path):
    """Initialize and run a single bot instance"""
    try:
        bot = BlueskyBot(config_path)
        bot.login()
        bot.run()
    except Exception as e:
        print_error(f"Bot failed to start: {e}")
        logging.error(f"Bot failed to start: {e}")

def main():
    """Run all bots from config directory"""
    # Find all yaml configs
    config_dir = Path('config')
    config_files = glob.glob(str(config_dir / '*.yaml'))
    
    if not config_files:
        print_error("No configuration files found in config directory!")
        return
    
    # Create a thread for each bot
    threads = []
    for config_file in config_files:
        print_action(f"Starting bot for config: {config_file}")
        thread = threading.Thread(
            target=run_bot,
            args=(config_file,),
            name=f"bot_{Path(config_file).stem}"
        )
        thread.daemon = True  # Allow program to exit even if threads are running
        threads.append(thread)
        thread.start()
    
    # Wait for all threads to complete (they won't, since they're infinite loops)
    try:
        while True:
            alive_threads = [t for t in threads if t.is_alive()]
            if not alive_threads:
                break
            for thread in alive_threads:
                thread.join(timeout=1.0)
    except KeyboardInterrupt:
        print_action("\nShutting down all bots...")
        # Each bot will handle its own cleanup in its run loop
    finally:
        print_success("All bots stopped!")

if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print_error(f"Program failed: {e}")
        logging.error(f"Program failed: {e}")