# -*- coding: utf-8 -*-
#
#    BitcoinLib - Python Cryptocurrency Library
#    Client for Bcoin Node
#    © 2019 June - 1200 Web Development <http://1200wd.com/>
#
#    This program is free software: you can redistribute it and/or modify
#    it under the terms of the GNU Affero General Public License as
#    published by the Free Software Foundation, either version 3 of the
#    License, or (at your option) any later version.
#
#    This program is distributed in the hope that it will be useful,
#    but WITHOUT ANY WARRANTY; without even the implied warranty of
#    MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
#    GNU Affero General Public License for more details.
#
#    You should have received a copy of the GNU Affero General Public License
#    along with this program.  If not, see <http://www.gnu.org/licenses/>.
#

from time import sleep
from requests import ReadTimeout
from bitcoinlib.main import *
from bitcoinlib.services.baseclient import BaseClient, ClientError
from bitcoinlib.transactions import Transaction
from bitcoinlib.encoding import to_hexstring


PROVIDERNAME = 'bcoin'

_logger = logging.getLogger(__name__)


class BcoinClient(BaseClient):
    """
    Class to interact with Bcoin API
    """

    def __init__(self, network, base_url, denominator, *args):
        super(self.__class__, self).__init__(network, PROVIDERNAME, base_url, denominator, *args)

    def compose_request(self, func, data='', parameter='', variables=None, method='get'):
        url_path = func
        if data:
            url_path += '/' + str(data)
        if parameter:
            url_path += '/' + parameter
        if variables is None:
            variables = {}
        return self.request(url_path, variables, method, secure=False)

    def _parse_transaction(self, tx):
        status = 'unconfirmed'
        if tx['confirmations']:
            status = 'confirmed'
        t = Transaction.import_raw(tx['hex'])
        t.locktime = tx['locktime']
        t.network = self.network
        t.fee = tx['fee']
        t.date = datetime.utcfromtimestamp(tx['time']) if tx['time'] else None
        t.confirmations = tx['confirmations']
        t.block_height = tx['height'] if tx['height'] > 0 else None
        t.block_hash = tx['block']
        t.status = status
        if t.coinbase:
            t.input_total = t.output_total
            t.inputs[0].value = t.output_total
        else:
            for i in t.inputs:
                i.value = tx['inputs'][t.inputs.index(i)]['coin']['value']
        for o in t.outputs:
            o.spent = None
        t.update_totals()
        return t

    def isspent(self, tx_id, index):
        try:
            self.compose_request('coin', tx_id, str(index))
        except ClientError:
            return True
        return False

    # def getbalance(self, addresslist):
    #     balance = 0.0
    #     for address in addresslist:
    #         res = tx = self.compose_request('address', address)
    #         balance += int(res['balance'])
    #     return int(balance * self.units)

    def getutxos(self, address, after_txid='', max_txs=MAX_TRANSACTIONS):
        txs = self.gettransactions(address, after_txid=after_txid, max_txs=max_txs)
        utxos = []
        for tx in txs:
            for unspent in tx.outputs:
                if unspent.address != address:
                    continue
                if not self.isspent(tx.hash, unspent.output_n):
                    utxos.append(
                        {
                            'address': unspent.address,
                            'tx_hash': tx.hash,
                            'confirmations': tx.confirmations,
                            'output_n': unspent.output_n,
                            'input_n': 0,
                            'block_height': tx.block_height,
                            'fee': tx.fee,
                            'size': tx.size,
                            'value': unspent.value,
                            'script': to_hexstring(unspent.lock_script),
                            'date': tx.date,
                         }
                    )
        return utxos

    def gettransaction(self, txid):
        tx = self.compose_request('tx', txid)
        return self._parse_transaction(tx)

    def gettransactions(self, address, after_txid='', max_txs=MAX_TRANSACTIONS):
        assert(max_txs > 0)
        txs = []
        while True:
            res = []
            variables = {'limit': max_txs, 'after': after_txid}
            retries = 0
            while retries < 3:
                try:
                    res = self.compose_request('tx', 'address', address, variables)
                except ReadTimeout as e:
                    sleep(3)
                    _logger.info("Bcoin client error: %s" % e)
                    retries += 1
                else:
                    break
                finally:
                    if retries == 3:
                        raise ClientError("Max retries exceeded with bcoin Client")
            for tx in res:
                txs.append(self._parse_transaction(tx))
            if not txs or len(txs) >= max_txs:
                break
            if len(res) == max_txs:
                after_txid = res[max_txs-1]['hash']
            else:
                break

        # Check which outputs are spent/unspent for this address
        spend_list = {}
        if not after_txid:
            for t in txs:
                for inp in t.inputs:
                    if inp.address == address:
                        spend_list.update({(to_hexstring(inp.prev_hash), inp.output_n_int): t})
            address_inputs = list(spend_list.keys())
            for t in txs:
                for to in t.outputs:
                    if to.address != address:
                        continue
                    spent = True if (t.hash, to.output_n) in address_inputs else False
                    txs[txs.index(t)].outputs[to.output_n].spent = spent
                    if spent:
                        spending_tx = spend_list[(t.hash, to.output_n)]
                        spending_index_n = \
                            [inp for inp in txs[txs.index(spending_tx)].inputs
                             if to_hexstring(inp.prev_hash) == t.hash and inp.output_n_int == to.output_n][0].index_n
                        txs[txs.index(t)].outputs[to.output_n].spending_txid = spending_tx.hash
                        txs[txs.index(t)].outputs[to.output_n].spending_index_n = spending_index_n
        return txs

    def getrawtransaction(self, txid):
        return self.compose_request('tx', txid)['hex']

    def sendrawtransaction(self, rawtx):
        res = self.compose_request('broadcast', variables={'tx': rawtx}, method='post')
        txid = ''
        if 'success' in res and res['success']:
            t = Transaction.import_raw(rawtx)
            txid = t.hash
        return {
            'txid': txid,
            'response_dict': res
        }

    def estimatefee(self, blocks):
        if blocks > 15:
            blocks = 15
        fee = self.compose_request('fee', variables={'blocks': blocks})['rate']
        if not fee:
            return False
        return fee

    def blockcount(self):
        return self.compose_request('')['chain']['height']

    def mempool(self, txid=''):
        txids = self.compose_request('mempool')
        if not txid:
            return txids
        elif txid in txids:
            return [txid]
        return []

    def getblock(self, blockid, parse_transactions, page, limit):
        block = self.compose_request('block', blockid)
        block['total_txs'] = len(block['txs'])
        txs = block['txs']
        parsed_txs = []
        if parse_transactions:
            txs = txs[(page-1)*limit:page*limit]
        for tx in txs:
            tx['confirmations'] = block['depth']
            tx['time'] = block['time']
            tx['height'] = block['height']
            tx['block'] = block['hash']
            if parse_transactions:
                # try:
                t = self._parse_transaction(tx)
                if t.hash != tx['hash']:
                    _logger.error("Could not parse tx %s. Different txid's" % (tx['hash']))
                parsed_txs.append(t)
                # except Exception as e:
                #     _logger.error("Could not parse tx %s with error %s" % (tx['hash'], e))
            else:
                parsed_txs.append(tx['hash'])

        block['time'] = datetime.utcfromtimestamp(block['time'])
        block['txs'] = parsed_txs
        block['page'] = page
        block['pages'] = int(block['total_txs'] // limit) + (block['total_txs'] % limit > 0)
        block['limit'] = limit
        block['prev_block'] = block.pop('prevBlock')
        block['merkle_root'] = block.pop('merkleRoot')
        return block
