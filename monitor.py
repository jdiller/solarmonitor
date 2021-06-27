#!/usr/bin/env python
import asyncio
import logging
import signal
import sys
import aiohttp
import datadog
import pysma
import numbers
import time

from configparser import ConfigParser


_LOGGER = logging.getLogger(__name__)

VAR = {}

class LoggingClientSession(aiohttp.ClientSession):
    async def _request(self, method, url, **kwargs):
        _LOGGER.debug('Starting request <%s %r>', method, url)
        _LOGGER.debug(kwargs)
        result = await super()._request(method, url, **kwargs)
        _LOGGER.debug('Completed request <%s %r>\nResult %s', method, url, result.text)
        return result

def send_values(sensors):
    for sen in sensors:
        if sen.value is not None:
            _LOGGER.debug("{:>25}{:>15} {}".format(sen.name, str(sen.value), sen.unit))
            if isinstance(sen.value, numbers.Number):
                datadog.statsd.histogram(sen.name, sen.value, tags=['environment:house'])
            else:
                _LOGGER.debug("Not sending non-numeric sensor '{}'", sen.name)



async def main_loop(loop, password, user, ip):  # pylint: disable=invalid-name
    """Main loop."""
    async with LoggingClientSession(loop=loop,
                          connector=aiohttp.TCPConnector(verify_ssl=False)) as session:
        VAR["sma"] = pysma.SMA(session, ip, password=password, group=user)
        back_off = 1
        retries = 0
        MAX_RETRIES = 10
        while VAR["sma"].sma_sid is None and retries < MAX_RETRIES:
            try:
                await VAR["sma"].new_session()
            except:
                _LOGGER.error("Failed to create session. Waiting {} seconds.".format(back_off))
                time.sleep(back_off)
                retries += 1
                back_off *= 2
        if VAR["sma"].sma_sid is None:
            _LOGGER.info("No session ID")
            return

        _LOGGER.info("NEW SID: %s", VAR["sma"].sma_sid)

        VAR["running"] = True
        cnt = 50
        sensors = pysma.Sensors()
        while VAR.get("running"):
            await VAR["sma"].read(sensors)
            # make sure there's always something in grid_power, assume 0 if it's missing
            if sensors['grid_power'].value is None:
                _LOGGER.info("Forcing a zero value for 'grid_power' metric.")
                sensors['grid_power'] = 0
            send_values(sensors)
            cnt -= 1
            #if cnt == 0:
            #    break
            await asyncio.sleep(2)

        await VAR["sma"].close_session()


def main():
    logging.basicConfig(format='%(asctime)s %(levelname)s {%(module)s} [%(funcName)s] %(message)s',
                           datefmt='%Y-%m-%d,%H:%M:%S', stream=sys.stdout, level=logging.INFO)

    config = ConfigParser()
    try:
        config.read("config.ini")
    except:
        print("Can't read config.ini")
        sys.exit(1)

    ip = config.get('solar', 'ip')
    password = config.get('solar', 'password')

    ddog_api_key = config.get('datadog', 'api_key')
    ddog_app_key = config.get('datadog', 'app_key')
    ddog_options = { 
            'api_key' : ddog_api_key,
            'app_key' : ddog_app_key,
            'statsd_host': '127.0.0.1',
            'statsd_port': 8125
    }

    datadog.initialize(**ddog_options)

    loop = asyncio.get_event_loop()

    def _shutdown(*_):
        VAR["running"] = False
        # asyncio.ensure_future(sma.close_session(), loop=loop)

    signal.signal(signal.SIGINT, _shutdown)
    # loop.add_signal_handler(signal.SIGINT, shutdown)
    # signal.signal(signal.SIGINT, signal.SIG_DFL)
    loop.run_until_complete(
        main_loop(loop, user='user', password=password, ip=ip)
    )


if __name__ == "__main__":
    main()
