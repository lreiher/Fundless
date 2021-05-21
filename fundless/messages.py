import time

import ccxt
import telegram.error
from telegram.ext import (
    Updater,
    CommandHandler,
    MessageHandler,
    ConversationHandler,
    Filters,
    CallbackContext
)
from telegram import Update, ReplyKeyboardMarkup, ReplyKeyboardRemove, KeyboardButton
from telegram.chataction import ChatAction
import logging
from typing import Callable, Any
from trading import TradingBot
from config import Config
import sys
from redo import retriable

logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)


# Decorator to check if message is from authorized sender
def authorized_only(command_handler: Callable[..., None]) -> Callable[..., Any]:
    def wrapper(self, *args, **kwargs):
        update = kwargs.get('update') or args[0]
        chat_id = int(self.chat_id)

        if int(update.message.chat_id) != chat_id:
            logger.info(f'Rejected unauthorized message from: {update.message.chat_id}')
            return wrapper

        logger.info(
            'Executing handler: %s for chat_id: %s',
            command_handler.__name__,
            chat_id
        )
        try:
            return command_handler(self, *args, **kwargs)
        except Exception as e:
            logger.exception('Exception occurred within Telegram Bot')
            raise e

    return wrapper


PLANNING, EXECUTING = range(2)


class TelegramBot:

    def __init__(self, config: Config, trading_bot: TradingBot):
        secrets = config.secrets.telegram
        self.chat_id = secrets['chat_id']
        self.updater = Updater(token=secrets['token'])
        self.dispatcher = self.updater.dispatcher
        self.trading_bot = trading_bot

        self.UnknownAnswerHandler = MessageHandler(Filters.text & ~Filters.command, self._unknown)

        self.savings_plan_conversation = ConversationHandler(
            entry_points=[CommandHandler('savings_plan', self._start_savings_plan_conversation)],
            states={
                PLANNING: [MessageHandler(Filters.regex('^(Yes, sounds great!|Noo!)$'), self._savings_plan_execution)]
            },
            fallbacks=[CommandHandler('cancel', self._cancel), self.UnknownAnswerHandler]
        )

        handles = [
            CommandHandler('start', self._start),
            CommandHandler('balance', self._balance),
            CommandHandler('index', self._index),
            self.savings_plan_conversation,
            CommandHandler('cancel', self._cancel),
            MessageHandler(Filters.command, self._unknown),
            MessageHandler(Filters.text & ~Filters.command, self._hodl_answer),
        ]

        for handle in handles:
            self.dispatcher.add_handler(handle)
        self.dispatcher.add_error_handler(self._error)
        self.updater.start_polling()
        # self.updater.idle()

    def cleanup(self):
        self.updater.stop()

    def _error(self, update: Update, context: CallbackContext):
        """Log Errors caused by Updates."""
        sys.stderr.write(f"ERROR: '{context.error}' caused by '{update}'")
        pass

    @authorized_only
    def _start(self, update, context):
        context.bot.send_message(chat_id=self.chat_id, text="I'm FundLess, please talk to me!")

    @retriable(attempts=5, sleeptime=4, retry_exceptions=(telegram.error.NetworkError, ))
    @authorized_only
    def _balance(self, _: Update, context: CallbackContext) -> None:
        context.bot.send_chat_action(chat_id=self.chat_id, action=ChatAction.TYPING)
        try:
            symbols, amounts, values, allocations = self.trading_bot.balance
        except ccxt.BaseError as e:
            msg = "I had a problem getting your balance from the exchange!"
            context.bot.send_message(chat_id=self.chat_id, text=msg)
            context.bot.send_message(chat_id=self.chat_id, text='Thas is, what the exchange returned:')
            context.bot.send_message(chat_id=self.chat_id, text=str(e))
        except KeyError as e:
            context.bot.send_message(chat_id=self.chat_id, text='Uh ohhh, I had a problem while computing your balances')
            context.bot.send_message(chat_id=self.chat_id, text=f'Could not find {e.args[0]} in market data of {self.trading_bot.bot_config.exchange.value}')
        else:
            msg = "```\n"
            msg += "--- Your current portfolio: ---\n"
            for symbol, allocation, value in zip(symbols, allocations, values):
                msg += f" {symbol+':': <6} {allocation:6.2f}% {value:10,.2f}$\n"
            msg += "-------------------------------\n"
            msg += f"  Overall Balance: {values.sum():,.2f} $"
            msg += "```"
            context.bot.send_message(chat_id=self.chat_id, text=msg, parse_mode='MarkdownV2')

    @retriable(attempts=5, sleeptime=4, retry_exceptions=(telegram.error.NetworkError,))
    @authorized_only
    def _index(self, _: Update, context: CallbackContext) -> None:
        context.bot.send_chat_action(chat_id=self.chat_id, action=ChatAction.TYPING)
        try:
            symbols, amounts, values, allocations = self.trading_bot.index_balance
        except ccxt.BaseError as e:
            msg = "I had a problem getting your balance from the exchange!"
            context.bot.send_message(chat_id=self.chat_id, text=msg)
            context.bot.send_message(chat_id=self.chat_id, text='Thas is, what the exchange returned:')
            context.bot.send_message(chat_id=self.chat_id, text=str(e))
        except KeyError as e:
            context.bot.send_message(chat_id=self.chat_id,
                                     text='Uh ohhh, I had a problem while computing your balances')
            context.bot.send_message(chat_id=self.chat_id,
                                     text=f'Could not find {e.args[0]} in market data of {self.trading_bot.bot_config.exchange.value}')
        else:
            msg = "```\n"
            msg += "Your current index portfolio:\n"
            for symbol, allocation, value in zip(symbols, allocations, values):
                msg += f" {symbol + ':': <6} {allocation:6.2f}% {value:10,.2f}$\n"
            msg += "-------------------------------\n"
            msg += f"  Overall Balance: {values.sum():,.2f} $"
            msg += "```"
            context.bot.send_message(chat_id=self.chat_id, text=msg, parse_mode='MarkdownV2')

    @retriable(attempts=5, sleeptime=4, retry_exceptions=(telegram.error.NetworkError, ))
    def ask_savings_plan_execution(self):
        reply_keyboard = [[
            KeyboardButton(r"/savings_plan"),
            KeyboardButton(r"/cancel")
        ]]
        markup = ReplyKeyboardMarkup(reply_keyboard, resize_keyboard=True, one_time_keyboard=True)
        msg = f"Should I execute your savings plan?"
        self.updater.bot.send_message(chat_id=self.chat_id, text=msg, reply_markup=markup)

    @retriable(attempts=5, sleeptime=4, retry_exceptions=(telegram.error.NetworkError, ))
    @authorized_only
    def _start_savings_plan_conversation(self, update: Update, context: CallbackContext):
        # TODO check if bot asked for execution before
        update.message.reply_text(
            "Alright! I am computing the optimal buy order..."
        )
        context.bot.send_chat_action(chat_id=self.chat_id, action=ChatAction.TYPING)
        time.sleep(2)
        symbols, weights = self.trading_bot.fetch_index_weights()
        msg = ("```\nThat's what I came up with:\n"
               "---------------------------")
        for symbol, weight in zip(symbols, weights):
            msg += f"\n  {symbol.upper()+':': <6}  {weight*self.trading_bot.bot_config.savings_plan_cost:6.2f} $"
        msg += "\n---------------------------"
        msg += f"\n Sum:  {weights.sum()*self.trading_bot.bot_config.savings_plan_cost} $"
        msg += "\n```"
        print(msg)
        update.message.reply_text(msg, parse_mode='MarkdownV2')
        update.message.reply_text(f"You are buying with {self.trading_bot.bot_config.base_symbol.upper()}")
        context.bot.send_chat_action(chat_id=self.chat_id, action=ChatAction.TYPING)
        time.sleep(2)
        reply_keyboard = [[
            "Yes, sounds great!",
            "Noo!"
        ]]
        markup = ReplyKeyboardMarkup(reply_keyboard, resize_keyboard=True, one_time_keyboard=True)
        update.message.reply_text(text="Should I proceed?", reply_markup=markup)
        return PLANNING

    @authorized_only
    def _savings_plan_execution(self, update: Update, context: CallbackContext):
        if update.message.text == 'Yes, sounds great!':
            update.message.reply_text(f"Great! I am buying your crypto on {self.trading_bot.bot_config.exchange.values[1]}")
            update.message.reply_text(f"Your order volume is {self.trading_bot.bot_config.savings_plan_cost:,.0f} $ ...")
            try:
                context.bot.send_chat_action(chat_id=self.chat_id, action=ChatAction.TYPING)
                symbols, weights = self.trading_bot.fetch_index_weights()
                problems = self.trading_bot.weighted_buy_order(symbols, weights)
            except ccxt.BaseError as e:
                update.message.reply_text("Ohhh, there was a Problem with the exchange! Sorry :(")
                update.message.reply_text('This is, what the exchange returned:')
                update.message.reply_text(str(e))
                update.message.reply_text('Try to solve it and try again next time')
                update.message.reply_text('See you :)')
            else:
                if problems['occurred']:
                    update.message.reply_text('I can not place your orders!')
                    context.bot.send_chat_action(chat_id=self.chat_id, action=ChatAction.TYPING)
                    time.sleep(1)
                    if len(problems['symbols'].keys()) > 0:
                        msg = "Problematic coins:"
                        for symbol in problems['symbols'].keys():
                            msg += f"\n\t- {symbol}, {problems['symbols'][symbol]}"
                        update.message.reply_text(msg)
                    else:
                        update.message.reply_text(problems['description'])
                    context.bot.send_chat_action(chat_id=self.chat_id, action=ChatAction.TYPING)
                    time.sleep(1)
                    update.message.reply_text('Solve the problems and try again next time!')
                    update.message.reply_text("See you")
                else:
                    update.message.reply_text("I did it!")
                    update.message.reply_text("Was a pleasure working with you")
                    update.message.reply_text("See you next time!")
        else:
            update.message.reply_text("Hmmm.. okay.. I will ask you another time")
        return ConversationHandler.END

    @staticmethod
    def _cancel(update: Update, _: CallbackContext) -> int:
        user = update.message.from_user
        logger.info("User %s canceled the conversation.", user.first_name)
        update.message.reply_text(
            'Bye! I hope we can talk again some day.', reply_markup=ReplyKeyboardRemove()
        )
        return ConversationHandler.END

    def _hodl_answer(self, update: Update, context: CallbackContext) -> None:
        reply_keyboard = [[
            KeyboardButton(r"/savings_plan"),
            KeyboardButton(r"/balance"),
            KeyboardButton(r"/index"),
            KeyboardButton(r"/cancel"),
        ]]
        markup = ReplyKeyboardMarkup(reply_keyboard, resize_keyboard=True, one_time_keyboard=True)
        context.bot.send_chat_action(chat_id=self.chat_id, action=ChatAction.TYPING)
        time.sleep(1)
        update.message.reply_text("HODL!", reply_markup=ReplyKeyboardRemove())
        update.message.reply_text("You can use the following commands:", reply_markup=markup)

    def _unknown(self, _: Update, context: CallbackContext):
        context.bot.send_message(chat_id=self.chat_id, text="Sorry, I didn't understand that.")
        reply_keyboard = [[
            "Yes, sounds great!",
            "/cancel"
        ]]
        markup = ReplyKeyboardMarkup(reply_keyboard, resize_keyboard=True, one_time_keyboard=True)
        context.bot.send_message(chat_id=self.chat_id, text='Would you like to proceed or cancel?', reply_markup=markup)

    def _unknown_command(self, _: Update, context: CallbackContext):
        context.bot.send_message(chat_id=self.chat_id, text="Sorry, I do not know that command.")
        reply_keyboard = [[
            KeyboardButton(r"/savings_plan"),
            KeyboardButton(r"/balance"),
            KeyboardButton(r"/index"),
            KeyboardButton(r"/cancel"),
        ]]
        markup = ReplyKeyboardMarkup(reply_keyboard, resize_keyboard=True, one_time_keyboard=True)
        context.bot.send_message(chat_id=self.chat_id, text='You can use these commands:', reply_markup=markup)