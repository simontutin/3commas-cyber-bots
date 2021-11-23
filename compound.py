#!/usr/bin/env python3
"""Cyberjunky's 3Commas bot helpers."""
import argparse
import configparser
import json
import logging
import math
import os
import queue
import sqlite3
import sys
import threading
import time
from logging.handlers import TimedRotatingFileHandler as _TimedRotatingFileHandler
from pathlib import Path

import apprise
from py3cw.request import Py3CW


class NotificationHandler:
    """Notification class."""

    def __init__(self, enabled=False, notify_urls=None):
        if enabled and notify_urls:
            self.apobj = apprise.Apprise()
            urls = json.loads(notify_urls)
            for url in urls:
                self.apobj.add(url)
            self.queue = queue.Queue()
            self.start_worker()
            self.enabled = True
        else:
            self.enabled = False

    def start_worker(self):
        """Start notification worker."""
        threading.Thread(target=self.process_queue, daemon=True).start()

    def process_queue(self):
        """Process the queue."""
        while True:
            message, attachments = self.queue.get()
            if attachments:
                self.apobj.notify(body=message, attach=attachments)
            else:
                self.apobj.notify(body=message)
            self.queue.task_done()

    def send_notification(self, message, attachments=None):
        """Send a notification if enabled."""
        if self.enabled:
            msg = f"[3Commas bots helper {program}]\n" + message
            self.queue.put((msg, attachments or []))


class TimedRotatingFileHandler(_TimedRotatingFileHandler):
    """Override original code to fix bug with not deleting old logfiles."""

    def __init__(self, filename="", when="midnight", interval=1, backupCount=7):
        super().__init__(
            filename=filename,
            when=when,
            interval=int(interval),
            backupCount=int(backupCount),
        )

    def getFilesToDelete(self):
        """Find all logfiles present."""
        dirname, basename = os.path.split(self.baseFilename)
        filenames = os.listdir(dirname)
        result = []
        prefix = basename + "."
        plen = len(prefix)
        for filename in filenames:
            if filename[:plen] == prefix:
                suffix = filename[plen:]
                if self.extMatch.match(suffix):
                    result.append(os.path.join(dirname, filename))
        result.sort()
        if len(result) < self.backupCount:
            result = []
        else:
            result = result[: len(result) - self.backupCount]
        return result

    def doRollover(self):
        """Delete old logfiles but keep latest backupCount amount."""
        super().doRollover()
        self.close()
        timetuple = time.localtime(time.time())
        dfn = self.baseFilename + "." + time.strftime(self.suffix, timetuple)

        if os.path.exists(dfn):
            os.remove(dfn)

        os.rename(self.baseFilename, dfn)

        if self.backupCount > 0:
            for oldlog in self.getFilesToDelete():
                os.remove(oldlog)

        self.stream = open(self.baseFilename, "w")

        currenttime = int(time.time())
        newrolloverat = self.computeRollover(currenttime)
        while newrolloverat <= currenttime:
            newrolloverat = newrolloverat + self.interval

        self.rolloverAt = newrolloverat


class Logger:
    """Logger class."""

    my_logger = None

    def __init__(self, notificationhandler, logstokeep, debug_enabled, notify_enabled):
        """Logger init."""
        self.my_logger = logging.getLogger()
        self.notify_enabled = notify_enabled
        self.notificationhandler = notificationhandler
        if debug_enabled:
            self.my_logger.setLevel(logging.DEBUG)
            self.my_logger.propagate = False
        else:
            self.my_logger.setLevel(logging.INFO)
            self.my_logger.propagate = False

        date_fmt = "%Y-%m-%d %H:%M:%S"
        formatter = logging.Formatter(
            "%(asctime)s - %(filename)s - %(levelname)s - %(message)s", date_fmt
        )
        console_formatter = logging.Formatter(
            "%(asctime)s - %(filename)s - %(message)s", date_fmt
        )
        # Create directory if not exists
        if not os.path.exists(f"{datadir}/logs"):
            os.makedirs(f"{datadir}/logs")

        # Log to file and rotate if needed
        file_handle = TimedRotatingFileHandler(
            filename=f"{datadir}/logs/{program}.log", backupCount=logstokeep
        )
        # file_handle.setLevel(logging.DEBUG)
        file_handle.setFormatter(formatter)
        self.my_logger.addHandler(file_handle)

        # Log to console
        console_handle = logging.StreamHandler()
        console_handle.setLevel(logging.INFO)
        console_handle.setFormatter(console_formatter)
        self.my_logger.addHandler(console_handle)

    def log(self, message, level="info"):
        """Call the log levels."""
        if level == "info":
            self.my_logger.info(message)
        elif level == "warning":
            self.my_logger.warning(message)
        elif level == "error":
            self.my_logger.error(message)
        elif level == "debug":
            self.my_logger.debug(message)

    def info(self, message, notify=False):
        """Info level."""
        self.log(message, "info")
        if self.notify_enabled and notify:
            self.notificationhandler.send_notification(message)

    def warning(self, message, notify=True):
        """Warning level."""
        self.log(message, "warning")
        if self.notify_enabled and notify:
            self.notificationhandler.send_notification(message)

    def error(self, message, notify=True):
        """Error level."""
        self.log(message, "error")
        if self.notify_enabled and notify:
            self.notificationhandler.send_notification(message)

    def debug(self, message, notify=False):
        """Debug level."""
        self.log(message, "debug")
        if self.notify_enabled and notify:
            self.notificationhandler.send_notification(message)


def load_config():
    """Create default or load existing config file."""

    cfg = configparser.ConfigParser()
    if cfg.read(f"{datadir}/{program}.ini"):
        return cfg

    cfg["settings"] = {
        "timezone": "Europe/Amsterdam",
        "timeinterval": 3600,
        "debug": False,
        "logrotate": 7,
        "botids": [12345, 67890],
        "profittocompound": 1.0,
        "3c-apikey": "Your 3Commas API Key",
        "3c-apisecret": "Your 3Commas API Secret",
        "notifications": False,
        "notify-urls": ["notify-url1", "notify-url2"],
    }

    with open(f"{datadir}/{program}.ini", "w") as cfgfile:
        cfg.write(cfgfile)

    return None


def init_threecommas_api(cfg):
    """Init the 3commas API."""
    return Py3CW(
        key=cfg.get("settings", "3c-apikey"),
        secret=cfg.get("settings", "3c-apisecret"),
        request_options={
            "request_timeout": 10,
            "nr_of_retries": 3,
            "retry_status_codes": [502],
        },
    )


def get_threecommas_deals(botid):
    """Get all deals from 3Commas from a bot."""

    error, data = api.request(
        entity="deals",
        action="",
        payload={
            "scope": "finished",
            "bot_id": str(botid),
            "limit": 100,
        },
    )
    if error:
        logger.error("Fetching deals failed with error: %s" % error)
    else:
        logger.info("Fetched the deals for this bot OK (%s deals)" % len(data))

    return data


def get_logged_profit_for_bot(bot_id):
    """Get the sum of all logged profit"""
    data = cursor.execute(
        f"SELECT sum(profit) FROM deals WHERE botid = {bot_id}"
    ).fetchone()[0]

    if data is None:
        return float(0)

    return data


def check_deal(dealid):
    """Check if deal was already logged."""
    data = cursor.execute(f"SELECT * FROM deals WHERE dealid = {dealid}").fetchone()
    if data is None:
        return False

    return True


def update_bot_order_volumes(
    thebot, new_base_order_volume, new_safety_order_volume, profit_sum, deals_count
):
    """Update bot with new order volumes."""

    bot_name = thebot["name"]
    base_order_volume = float(thebot["base_order_volume"])
    safety_order_volume = float(thebot["safety_order_volume"])

    logger.info(
        "Calculated BO volume changed from: %s to %s"
        % (base_order_volume, new_base_order_volume)
    )
    logger.info(
        "Calculated SO volume changed from: %s to %s"
        % (safety_order_volume, new_safety_order_volume)
    )

    error, data = api.request(
        entity="bots",
        action="update",
        action_id=str(thebot["id"]),
        payload={
            "bot_id": thebot["id"],
            "name": thebot["name"],
            "pairs": thebot["pairs"],
            "base_order_volume": new_base_order_volume,  # new base order volume
            "safety_order_volume": new_safety_order_volume,  # new safety order volume
            "take_profit": thebot["take_profit"],
            "martingale_volume_coefficient": thebot["martingale_volume_coefficient"],
            "martingale_step_coefficient": thebot["martingale_step_coefficient"],
            "max_active_deals": thebot["max_active_deals"],
            "max_safety_orders": thebot["max_safety_orders"],
            "safety_order_step_percentage": thebot["safety_order_step_percentage"],
            "take_profit_type": thebot["take_profit_type"],
            "strategy_list": thebot["strategy_list"],
            "active_safety_orders_count": thebot["active_safety_orders_count"],
        },
    )
    if data:
        base = thebot["pairs"][0].split("_")[0]
        if base == "BTC":
            logger.info(
                f"Compounded ₿{round(profit_sum, 8)} in profit from {deals_count} deal(s) "
                f"made by '{bot_name}'\nChanged BO from ₿{round(base_order_volume, 8)} to "
                f"₿{round(new_base_order_volume, 8)}\nChanged SO from "
                f"₿{round(safety_order_volume, 8)} to ₿{round(new_safety_order_volume, 8)}",
                True,
            )
        else:
            logger.info(
                f"Compounded ${round(profit_sum, 4)} in profit from {deals_count} deal(s) "
                f"made by '{bot_name}'\nChanged BO from ${round(base_order_volume, 4)} to "
                f"${round(new_base_order_volume, 4)}\nChanged SO from "
                f"${round(safety_order_volume, 4)} to ${round(new_safety_order_volume, 4)}",
                True,
            )
    else:
        logger.error(
            "Error occurred updating bot with new BO/SO values: %s" % error["msg"]
        )


def process_deals(deals):
    """Check deals from bot."""

    deals_count = 0
    profit_sum = 0.0

    for deal in deals:
        deal_id = deal["id"]
        bot_id = deal["bot_id"]
        # Register deal in database
        exist = check_deal(deal_id)
        if exist:
            logger.debug("Deal with id '%s' already processed, skipping." % deal_id)
        else:
            # Deal not processed yet
            profit = float(deal["final_profit"])
            deals_count += 1
            profit_sum += profit

            db.execute(
                f"INSERT INTO deals (dealid, profit, bot_id) VALUES ({deal_id}, {profit}, {bot_id})"
            )

    logger.info("Finished deals: %s total profit: %s" % (deals_count, profit_sum))
    db.commit()

    # Calculate profit part to compound
    logger.info("Profit available to compound: %s" % profit_sum)

    return (deals_count, profit_sum)


def get_bot_values(thebot):
    """Load start boso values from database or calcutate and store them."""

    startbo = 0.0
    startso = 0.0
    startactivedeals = thebot["max_active_deals"]
    bot_id = thebot["id"]

    data = cursor.execute(
        f"SELECT startbo, startso, startactivedeals FROM bots WHERE botid = {bot_id}"
    ).fetchone()
    if data:
        # Fetch values from database
        startbo = data[0]
        startso = data[1]
        startactivedeals = data[2]
        logger.info(
            "Fetched bot start BO, SO values and max. active deals: %s %s %s"
            % (startbo, startso, startactivedeals)
        )
    else:
        # Store values in database
        startbo = float(thebot["base_order_volume"])
        startso = float(thebot["safety_order_volume"])
        startactivedeals = thebot["max_active_deals"]
        db.execute(
            f"INSERT INTO bots (botid, startbo, startso, startactivedeals) VALUES ({bot_id}, {startbo}, {startso}, {startactivedeals})"
        )

        logger.info(
            "Stored bot start BO, SO values and max. active deals: %s %s %s"
            % (startbo, startso, startactivedeals)
        )
        db.commit()

    return (startbo, startso, startactivedeals)


def load_bot_config(thisconfig, thebot):
    """Create default or load config section for bot."""

    bot_id = thebot["id"]
    bot_name = thebot["name"]

    # Check if config section for bot exists
    if not thisconfig.has_section(f"bot_{bot_id}"):

        # Create bot config object and read current config
        botcfg = configparser.ConfigParser()
        botcfg.read(f"{datadir}/{program}.ini")

        # Add new config section
        botcfg[f"bot_{bot_id}"] = {
            "compoundmode": "boso",
            "profittocompound": 1.0,
            "usermaxactivedeals": int(thebot["max_active_deals"]) + 5,
            "comment": bot_name,
        }

        with open(f"{datadir}/{program}.ini", "w") as cfgfile:
            botcfg.write(cfgfile)

        logger.info(
            f"Created missing bot config section ['bot_{bot_id}'] with defaults in '{datadir}/{program}.ini', check it."
        )
        sys.exit(0)


def update_bot_max_deals(thebot, org_base_order, org_safety_order, new_max_deals):
    """Update bot with new max deals and old bo/so values."""

    bot_name = thebot["name"]
    base_order_volume = float(thebot["base_order_volume"])
    safety_order_volume = float(thebot["safety_order_volume"])
    max_active_deals = thebot["max_active_deals"]

    logger.info(
        "Calculated max. active deals changed from: %s to %s"
        % (max_active_deals, new_max_deals)
    )
    logger.info(
        "Calculated BO volume changed from: %s to %s"
        % (base_order_volume, org_base_order)
    )
    logger.info(
        "Calculated SO volume changed from: %s to %s"
        % (safety_order_volume, org_safety_order)
    )

    error, data = api.request(
        entity="bots",
        action="update",
        action_id=str(thebot["id"]),
        payload={
            "bot_id": thebot["id"],
            "name": thebot["name"],
            "pairs": thebot["pairs"],
            "base_order_volume": org_base_order,  # original base order volume
            "safety_order_volume": org_safety_order,  # original safety order volume
            "take_profit": thebot["take_profit"],
            "martingale_volume_coefficient": thebot["martingale_volume_coefficient"],
            "martingale_step_coefficient": thebot["martingale_step_coefficient"],
            "max_active_deals": new_max_deals,  # new max. deals value
            "max_safety_orders": thebot["max_safety_orders"],
            "safety_order_step_percentage": thebot["safety_order_step_percentage"],
            "take_profit_type": thebot["take_profit_type"],
            "strategy_list": thebot["strategy_list"],
            "active_safety_orders_count": thebot["active_safety_orders_count"],
        },
    )
    if data:
        logger.info(
            f"Changed max. active deals from: %s to %s for bot\n'{bot_name}'\nChanged BO from ${round(base_order_volume, 4)} to "
            f"${round(org_base_order, 4)}\nChanged SO from "
            f"${round(safety_order_volume, 4)} to ${round(org_safety_order, 4)}"
            % (max_active_deals, new_max_deals)
        )
    else:
        logger.error(
            "Error occurred updating bot with new max. deals and bo/so values: %s"
            % error["msg"]
        )


def compound_bot(thisconfig, thebot):
    """Find profit from deals and calculate new SO and BO values."""

    bot_name = thebot["name"]
    bot_id = thebot["id"]

    # Check for config section for bot, add if missing.
    load_bot_config(thisconfig, thebot)

    deals = get_threecommas_deals(bot_id)
    bot_profit_percentage = float(
        thisconfig.get(
            f"bot_{bot_id}",
            "profittocompound",
            fallback=thisconfig.get("settings", "profittocompound"),
        )
    )
    if thisconfig.get(f"bot_{bot_id}", "compoundmode", fallback="boso") == "deals":

        # Get starting BO and SO values
        (startbo, startso, startactivedeals) = get_bot_values(thebot)

        # Get active deal settings
        user_defined_max_active_deals = int(
            thisconfig.get(f"bot_{bot_id}", "usermaxactivedeals")
        )

        # Calculate amount used per deal
        max_safety_orders = float(thebot["max_safety_orders"])
        martingale_volume_coefficient = float(
            thebot["martingale_volume_coefficient"]
        )  # Safety order volume scale

        # Always add start_base_order_size
        totalusedperdeal = startbo

        isafetyorder = 1
        while isafetyorder <= max_safety_orders:
            # For the first Safety order, just use the startso
            if isafetyorder == 1:
                total_safety_order_volume = startso

            # After the first SO, multiple the previous SO with the safety order volume scale
            if isafetyorder > 1:
                total_safety_order_volume *= martingale_volume_coefficient

            totalusedperdeal += total_safety_order_volume
            isafetyorder += 1

        # Calculate % to compound (per bot)
        totalprofitforbot = get_logged_profit_for_bot(thebot["id"])
        profitusedtocompound = totalprofitforbot * bot_profit_percentage

        new_max_active_deals = (
            math.floor(profitusedtocompound / totalusedperdeal) + startactivedeals
        )
        current_active_deals = thebot["max_active_deals"]

        if new_max_active_deals > user_defined_max_active_deals:
            logger.info(
                f"Already reached max set number of deals ({user_defined_max_active_deals}), skipping deal compounding"
            )
        elif (
            new_max_active_deals
            > current_active_deals & new_max_active_deals
            <= user_defined_max_active_deals
        ):
            logger.info(
                "Enough profit has been made to add a deal and lower BO & SO to their orginal values"
            )
            # Update the bot
            update_bot_max_deals(thebot, startbo, startso, new_max_active_deals)

    if deals:
        (deals_count, profit_sum) = process_deals(deals)

        profit_sum *= bot_profit_percentage
        logger.info(
            "Profit available after applying percentage value (%s): %s "
            % (bot_profit_percentage, profit_sum)
        )

        if profit_sum:
            # Bot values to calculate with
            base_order_volume = float(thebot["base_order_volume"])
            safety_order_volume = float(thebot["safety_order_volume"])
            max_active_deals = thebot["max_active_deals"]
            max_safety_orders = thebot["max_safety_orders"]
            martingale_volume_coefficient = float(
                thebot["martingale_volume_coefficient"]
            )

            funds_so_needed = safety_order_volume
            total_so_funds = safety_order_volume
            if max_safety_orders > 1:
                for i in range(1, max_safety_orders):
                    funds_so_needed *= float(martingale_volume_coefficient)
                    total_so_funds += funds_so_needed

            logger.info("Current bot settings :")
            logger.info("Base order volume    : %s" % base_order_volume)
            logger.info("Safety order volume  : %s" % safety_order_volume)
            logger.info("Max active deals     : %s" % max_active_deals)
            logger.info("Max safety orders    : %s" % max_safety_orders)
            logger.info("SO volume scale      : %s" % martingale_volume_coefficient)

            # Calculate the BO/SO ratio
            bo_percentage = (
                100
                * float(base_order_volume)
                / (float(base_order_volume) + float(total_so_funds))
            )
            so_percentage = (
                100
                * float(total_so_funds)
                / (float(total_so_funds) + float(base_order_volume))
            )
            logger.info("BO percentage: %s" % bo_percentage)
            logger.info("SO percentage: %s" % so_percentage)

            # Calculate compound values
            bo_profit = ((profit_sum * bo_percentage) / 100) / max_active_deals
            so_profit = bo_profit * (safety_order_volume / base_order_volume)

            logger.info("BO compound value: %s" % bo_profit)
            logger.info("SO compound value: %s" % so_profit)

            # Update the bot
            update_bot_order_volumes(
                thebot,
                (base_order_volume + bo_profit),
                (safety_order_volume + so_profit),
                profit_sum,
                deals_count,
            )
        else:
            logger.info(
                f"{bot_name}\nNo (new) profit made, no BO/SO value updates needed!",
                True,
            )
    else:
        logger.info(f"{bot_name}\nNo (new) deals found for this bot!", True)


def init_compound_db():
    """Create or open database to store bot and deals data."""
    try:
        dbname = f"{program}.sqlite3"
        dbpath = f"file:{datadir}/{dbname}?mode=rw"
        dbconnection = sqlite3.connect(dbpath, uri=True)

        logger.info(f"Database '{datadir}/{dbname}' opened successfully")

    except sqlite3.OperationalError:
        dbconnection = sqlite3.connect(f"{datadir}/{dbname}")
        dbcursor = dbconnection.cursor()
        logger.info(f"Database '{datadir}/{dbname}' created successfully")

        dbcursor.execute(
            "CREATE TABLE deals (dealid INT Primary Key, profit REAL, botid int)"
        )
        dbcursor.execute(
            "CREATE TABLE bots (botid INT Primary Key, startbo REAL, startso REAL, startactivedeals int)"
        )
        logger.info("Database tables created successfully")

    return dbconnection


def upgrade_compound_db():
    """Upgrade database if needed."""
    try:
        cursor.execute("ALTER TABLE deals ADD COLUMN profit REAL")
        logger.info("Database table deals upgraded (column profit)")
    except sqlite3.OperationalError:
        pass

    try:
        cursor.execute("ALTER TABLE deals ADD COLUMN botid int")
        logger.info("Database table deals upgraded (column botid)")
    except sqlite3.OperationalError:
        pass

    try:
        cursor.execute(
            "CREATE TABLE bots (botid INT Primary Key, startbo REAL, startso REAL, startactivedeals int)"
        )
        logger.info("Database schema upgraded (table bots)")
    except sqlite3.OperationalError:
        pass


# Start application
program = Path(__file__).stem

# Parse and interpret options.
parser = argparse.ArgumentParser(description="Cyberjunky's 3Commas bot helper.")
parser.add_argument("-d", "--datadir", help="data directory to use", type=str)

args = parser.parse_args()
if args.datadir:
    datadir = args.datadir
else:
    datadir = os.getcwd()

# Create or load configuration file
config = load_config()
if not config:
    logger = Logger(None, 7, False, False)
    logger.info(f"3Commas bot helper {program}!")
    logger.info("Started at %s." % time.strftime("%A %H:%M:%S %d-%m-%Y"))
    logger.info(
        f"Created example config file '{datadir}/{program}.ini', edit it and restart the program."
    )
    sys.exit(0)
else:
    # Handle timezone
    if hasattr(time, "tzset"):
        os.environ["TZ"] = config.get(
            "settings", "timezone", fallback="Europe/Amsterdam"
        )
        time.tzset()

    # Init notification handler
    notification = NotificationHandler(
        config.getboolean("settings", "notifications"),
        config.get("settings", "notify-urls"),
    )

    # Init logging
    logger = Logger(
        notification,
        int(config.get("settings", "logrotate", fallback=7)),
        config.getboolean("settings", "debug"),
        config.getboolean("settings", "notifications"),
    )
    logger.info(f"3Commas bot helper {program}")
    logger.info("Started at %s" % time.strftime("%A %H:%M:%S %d-%m-%Y"))
    logger.info(f"Loaded configuration from '{datadir}/{program}.ini'")

if notification.enabled:
    logger.info("Notifications are enabled")
else:
    logger.info("Notifications are disabled")

# Initialize 3Commas API
api = init_threecommas_api(config)
# Initialize or open database
db = init_compound_db()
cursor = db.cursor()
# Upgrade database if needed
upgrade_compound_db()

if "compound" in program:
    # Auto compound profit by tweaking SO/BO
    while True:

        config = load_config()
        logger.info(f"Reloaded configuration from '{datadir}/{program}.ini'")

        # User settings
        botids = json.loads(config.get("settings", "botids"))
        timeint = int(config.get("settings", "timeinterval"))
        profit_percentage = float(
            config.get("settings", "profittocompound", fallback=1.0)
        )

        # Walk through all bots specified
        for bot in botids:
            boterror, botdata = api.request(
                entity="bots",
                action="show",
                action_id=str(bot),
            )
            if botdata:
                compound_bot(config, botdata)
            else:
                logger.error("Error occurred compounding bots: %s" % boterror["msg"])

        if timeint > 0:
            localtime = time.time()
            nexttime = localtime + int(timeint)
            timeresult = time.strftime("%H:%M:%S", time.localtime(nexttime))
            logger.info("Next update in %s Seconds at %s" % (timeint, timeresult), True)
            time.sleep(timeint)
        else:
            break
