# Bluesky Bot

This bot is designed to interact with the Bluesky social media platform, performing actions like liking, replying, and following based on customizable engagement strategies.

## Features

- **Automated Engagement**: Like, reply, and follow based on predefined strategies.
- **Customizable Personality**: Use YAML configuration files to define the bot's engagement style and personality.
- **Dynamic Rate Control**: Adjusts activity levels based on engagement effectiveness.
- **Error Handling**: Robust error handling and logging for smooth operation.

## Setup

### Prerequisites

- Python 3.8+
- [Bluesky Account](https://bsky.app)
- [Bluesky App Password](https://bsky.app/settings/app-passwords)

### Installation

1. **Clone the repository:**

   ```bash
   git clone https://github.com/yourusername/bluesky-bot.git
   cd bluesky-bot
   ```

2. **Install dependencies:**

   ```bash
   pip install -r requirements.txt
   ```

3. **Set up environment variables:**

   Create a `.env` file in the root directory with your Bluesky credentials:

   ```env
   BLUESKY_TECH_HANDLE=your_handle.bsky.social
   BLUESKY_TECH_APP_KEY=your-app-password
   ```

4. **Configure the bot:**

   Edit the YAML configuration files in the `config/` directory to customize the bot's behavior. For example, `config/byitts_bot.yaml`:

   ```yaml
   engagement_style:
     system_prompt: |
       You're a friendly marketplace enthusiast...
     temperature: 0.9
     max_emojis: 2

   limits:
     daily:
       follows: 150
       likes: 500
       replies: 200
       reposts: 100
   ```

## Usage

1. **Run the bot:**

   ```bash
   python bot.py
   ```

2. **Monitor the bot:**

   Check the console output for logs and any potential errors. Logs are also saved in the `logs/` directory.

## Troubleshooting

- **Authentication Errors**: Ensure your `.env` file has the correct and current app password.
- **Rate Limits**: Adjust the `limits` in your YAML configuration to avoid hitting Bluesky's rate limits.
- **Environment Variables**: If changes to `.env` aren't reflected, try restarting your terminal session or manually unsetting old variables.

## Contributing

Feel free to submit issues or pull requests. Contributions are welcome!

## License

This project is licensed under the MIT License. See the [LICENSE](LICENSE) file for details.
