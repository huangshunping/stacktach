# Copyright (c) 2012 - Rackspace Inc.
#
# Permission is hereby granted, free of charge, to any person obtaining a copy
# of this software and associated documentation files (the "Software"), to
# deal in the Software without restriction, including without limitation the
# rights to use, copy, modify, merge, publish, distribute, sublicense, and/or
# sell copies of the Software, and to permit persons to whom the Software is
# furnished to do so, subject to the following conditions:
#
# The above copyright notice and this permission notice shall be included in
# all copies or substantial portions of the Software.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
# AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
# LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING
# FROM, OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS
# IN THE SOFTWARE.

import argparse
import datetime
import json
import os
import sys
from time import sleep
import uuid

from django.db import transaction
import kombu.common
import kombu.entity
import kombu.pools
import multiprocessing

POSSIBLE_TOPDIR = os.path.normpath(os.path.join(os.path.abspath(sys.argv[0]),
                                                os.pardir, os.pardir))
if os.path.exists(os.path.join(POSSIBLE_TOPDIR, 'stacktach')):
    sys.path.insert(0, POSSIBLE_TOPDIR)

from stacktach import stacklog

stacklog.set_default_logger_name('verifier')
LOG = stacklog.get_logger()

from stacktach import models
from stacktach import datetime_to_decimal as dt
from verifier import AmbiguousResults
from verifier import FieldMismatch
from verifier import NotFound
from verifier import VerificationException


def _list_exists(ending_max=None, status=None):
    params = {}
    if ending_max:
        params['audit_period_ending__lte'] = dt.dt_to_decimal(ending_max)
    if status:
        params['status'] = status
    return models.InstanceExists.objects.select_related()\
                                .filter(**params).order_by('id')


def _find_launch(instance, launched):
    start = launched - datetime.timedelta(microseconds=launched.microsecond)
    end = start + datetime.timedelta(microseconds=999999)
    params = {'instance': instance,
              'launched_at__gte': dt.dt_to_decimal(start),
              'launched_at__lte': dt.dt_to_decimal(end)}
    return models.InstanceUsage.objects.filter(**params)


def _find_reconcile(instance, launched):
    start = launched - datetime.timedelta(microseconds=launched.microsecond)
    end = start + datetime.timedelta(microseconds=999999)
    params = {'instance': instance,
              'launched_at__gte': dt.dt_to_decimal(start),
              'launched_at__lte': dt.dt_to_decimal(end)}
    return models.InstanceReconcile.objects.filter(**params)


def _find_delete(instance, launched, deleted_max=None):
    start = launched - datetime.timedelta(microseconds=launched.microsecond)
    end = start + datetime.timedelta(microseconds=999999)
    params = {'instance': instance,
              'launched_at__gte': dt.dt_to_decimal(start),
              'launched_at__lte': dt.dt_to_decimal(end)}
    if deleted_max:
        params['deleted_at__lte'] = dt.dt_to_decimal(deleted_max)
    return models.InstanceDeletes.objects.filter(**params)


def _mark_exist_verified(exist,
                         reconciled=False,
                         reason=None):
    if not reconciled:
        exist.status = models.InstanceExists.VERIFIED
    else:
        exist.status = models.InstanceExists.RECONCILED
        if reason is not None:
            exist.fail_reason = reason

    exist.save()


def _mark_exist_failed(exist, reason=None):
    exist.status = models.InstanceExists.FAILED
    if reason:
        exist.fail_reason = reason
    exist.save()


def _has_field(d1, d2, field1, field2=None):
    if not field2:
        field2 = field1

    return d1.get(field1) is not None and d2.get(field2) is not None


def _verify_simple_field(d1, d2, field1, field2=None):
    if not field2:
        field2 = field1

    if not _has_field(d1, d2, field1, field2):
        return False
    else:
        if d1[field1] != d2[field2]:
            return False

    return True


def _verify_date_field(d1, d2, same_second=False):
    if d1 and d2:
        if d1 == d2:
            return True
        elif same_second and int(d1) == int(d2):
            return True
    return False


def _verify_field_mismatch(exists, launch):
    if not _verify_date_field(launch.launched_at, exists.launched_at,
                              same_second=True):
        raise FieldMismatch('launched_at', exists.launched_at,
                            launch.launched_at)

    if launch.instance_type_id != exists.instance_type_id:
        raise FieldMismatch('instance_type_id', exists.instance_type_id,
                            launch.instance_type_id)

    if launch.tenant != exists.tenant:
        raise FieldMismatch('tenant', exists.tenant,
                            launch.tenant)

    if launch.rax_options != exists.rax_options:
        raise FieldMismatch('rax_options', exists.rax_options,
                            launch.rax_options)

    if launch.os_architecture != exists.os_architecture:
        raise FieldMismatch('os_architecture', exists.os_architecture,
                            launch.os_architecture)

    if launch.os_version != exists.os_version:
        raise FieldMismatch('os_version', exists.os_version,
                            launch.os_version)

    if launch.os_distro != exists.os_distro:
        raise FieldMismatch('os_distro', exists.os_distro,
                            launch.os_distro)


def _verify_for_launch(exist, launch=None, launch_type="InstanceUsage"):

    if not launch and exist.usage:
        launch = exist.usage
    elif not launch:
        if models.InstanceUsage.objects\
                 .filter(instance=exist.instance).count() > 0:
            launches = _find_launch(exist.instance,
                                    dt.dt_from_decimal(exist.launched_at))
            count = launches.count()
            query = {
                'instance': exist.instance,
                'launched_at': exist.launched_at
            }
            if count > 1:
                raise AmbiguousResults(launch_type, query)
            elif count == 0:
                raise NotFound(launch_type, query)
            launch = launches[0]
        else:
            raise NotFound(launch_type, {'instance': exist.instance})

    _verify_field_mismatch(exist, launch)


def _verify_for_delete(exist, delete=None, delete_type="InstanceDelete"):

    if not delete and exist.delete:
        # We know we have a delete and we have it's id
        delete = exist.delete
    elif not delete:
        if exist.deleted_at:
            # We received this exists before the delete, go find it
            deletes = _find_delete(exist.instance,
                                   dt.dt_from_decimal(exist.launched_at))
            if deletes.count() == 1:
                delete = deletes[0]
            else:
                query = {
                    'instance': exist.instance,
                    'launched_at': exist.launched_at
                }
                raise NotFound(delete_type, query)
        else:
            # We don't know if this is supposed to have a delete or not.
            # Thus, we need to check if we have a delete for this instance.
            # We need to be careful though, since we could be verifying an
            # exist event that we got before the delete. So, we restrict the
            # search to only deletes before this exist's audit period ended.
            # If we find any, we fail validation
            launched_at = dt.dt_from_decimal(exist.launched_at)
            deleted_at_max = dt.dt_from_decimal(exist.audit_period_ending)
            deletes = _find_delete(exist.instance, launched_at, deleted_at_max)
            if deletes.count() > 0:
                reason = 'Found %ss for non-delete exist' % delete_type
                raise VerificationException(reason)

    if delete:
        if not _verify_date_field(delete.launched_at, exist.launched_at,
                                  same_second=True):
            raise FieldMismatch('launched_at', exist.launched_at,
                                delete.launched_at)

        if not _verify_date_field(delete.deleted_at, exist.deleted_at,
                                  same_second=True):
            raise FieldMismatch('deleted_at', exist.deleted_at,
                                delete.deleted_at)


def _verify_with_reconciled_data(exist, ex):
    if not exist.launched_at:
        raise VerificationException("Exists without a launched_at")

    query = models.InstanceReconcile.objects.filter(instance=exist.instance)
    if query.count() > 0:
        recs = _find_reconcile(exist.instance,
                               dt.dt_from_decimal(exist.launched_at))
        search_query = {'instance': exist.instance,
                        'launched_at': exist.launched_at}
        count = recs.count()
        if count > 1:
            raise AmbiguousResults('InstanceReconcile', search_query)
        elif count == 0:
            raise NotFound('InstanceReconcile', search_query)
        reconcile = recs[0]
    else:
        raise NotFound('InstanceReconcile', {'instance': exist.instance})

    _verify_for_launch(exist, launch=reconcile,
                       launch_type="InstanceReconcile")
    _verify_for_delete(exist, delete=reconcile,
                       delete_type="InstanceReconcile")


def _verify(exist):
    verified = False
    try:
        if not exist.launched_at:
            raise VerificationException("Exists without a launched_at")

        _verify_for_launch(exist)
        _verify_for_delete(exist)

        verified = True
        _mark_exist_verified(exist)
    except VerificationException, orig_e:
        # Something is wrong with the InstanceUsage record
        try:
            # Attempt to verify against reconciled data
            _verify_with_reconciled_data(exist, orig_e)
            verified = True
            _mark_exist_verified(exist)
        except NotFound, rec_e:
            # No reconciled data, just mark it failed
            _mark_exist_failed(exist, reason=str(orig_e))
        except VerificationException, rec_e:
            # Verification failed against reconciled data, mark it failed
            #    using the second failure.
            _mark_exist_failed(exist, reason=str(rec_e))
        except Exception, rec_e:
            _mark_exist_failed(exist, reason=rec_e.__class__.__name__)
            LOG.exception(rec_e)
    except Exception, e:
        _mark_exist_failed(exist, reason=e.__class__.__name__)
        LOG.exception(e)

    return verified, exist


results = []


def verify_for_range(pool, ending_max, callback=None):
    exists = _list_exists(ending_max=ending_max,
                          status=models.InstanceExists.PENDING)
    count = exists.count()
    added = 0
    update_interval = datetime.timedelta(seconds=30)
    next_update = datetime.datetime.utcnow() + update_interval
    LOG.info("Adding %s exists to queue." % count)
    while added < count:
        for exist in exists[0:1000]:
            exist.status = models.InstanceExists.VERIFYING
            exist.save()
            result = pool.apply_async(_verify, args=(exist,),
                                      callback=callback)
            results.append(result)
            added += 1
            if datetime.datetime.utcnow() > next_update:
                values = ((added,) + clean_results())
                msg = "N: %s, P: %s, S: %s, E: %s" % values
                LOG.info(msg)
                next_update = datetime.datetime.utcnow() + update_interval

    return count


def clean_results():
    global results

    pending = []
    finished = 0
    successful = 0

    for result in results:
        if result.ready():
            finished += 1
            if result.successful():
                successful += 1
        else:
            pending.append(result)

    results = pending
    errored = finished - successful
    return len(results), successful, errored


def _send_notification(message, routing_key, connection, exchange):
    with kombu.pools.producers[connection].acquire(block=True) as producer:
        kombu.common.maybe_declare(exchange, producer.channel)
        producer.publish(message, routing_key)


def send_verified_notification(exist, connection, exchange, routing_keys=None):
    body = exist.raw.json
    json_body = json.loads(body)
    json_body[1]['event_type'] = 'compute.instance.exists.verified.old'
    json_body[1]['original_message_id'] = json_body[1]['message_id']
    json_body[1]['message_id'] = str(uuid.uuid4())
    if routing_keys is None:
        _send_notification(json_body[1], json_body[0], connection, exchange)
    else:
        for key in routing_keys:
            _send_notification(json_body[1], key, connection, exchange)


def _create_exchange(name, type, exclusive=False, auto_delete=False,
                     durable=True):
    return kombu.entity.Exchange(name, type=type, exclusive=auto_delete,
                                 auto_delete=exclusive, durable=durable)


def _create_connection(config):
    rabbit = config['rabbit']
    conn_params = dict(hostname=rabbit['host'],
                       port=rabbit['port'],
                       userid=rabbit['userid'],
                       password=rabbit['password'],
                       transport="librabbitmq",
                       virtual_host=rabbit['virtual_host'])
    return kombu.connection.BrokerConnection(**conn_params)


def _run(config, pool, callback=None):
    tick_time = config['tick_time']
    settle_units = config['settle_units']
    settle_time = config['settle_time']
    while True:
        with transaction.commit_on_success():
            now = datetime.datetime.utcnow()
            kwargs = {settle_units: settle_time}
            ending_max = now - datetime.timedelta(**kwargs)
            new = verify_for_range(pool, ending_max, callback=callback)

            msg = "N: %s, P: %s, S: %s, E: %s" % ((new,) + clean_results())
            LOG.info(msg)
        sleep(tick_time)


def run(config):
    pool = multiprocessing.Pool(config['pool_size'])

    if config['enable_notifications']:
        exchange = _create_exchange(config['rabbit']['exchange_name'],
                                    'topic',
                                    durable=config['rabbit']['durable_queue'])
        routing_keys = None
        if config['rabbit'].get('routing_keys') is not None:
            routing_keys = config['rabbit']['routing_keys']

        with _create_connection(config) as conn:
            def callback(result):
                (verified, exist) = result
                if verified:
                    send_verified_notification(exist, conn, exchange,
                                               routing_keys=routing_keys)

            _run(config, pool, callback=callback)
    else:
        _run(config, pool)


def _run_once(config, pool, callback=None):
    tick_time = config['tick_time']
    settle_units = config['settle_units']
    settle_time = config['settle_time']
    now = datetime.datetime.utcnow()
    kwargs = {settle_units: settle_time}
    ending_max = now - datetime.timedelta(**kwargs)
    new = verify_for_range(pool, ending_max, callback=callback)

    LOG.info("Verifying %s exist events" % new)
    while len(results) > 0:
        LOG.info("P: %s, F: %s, E: %s" % clean_results())
        sleep(tick_time)


def run_once(config):
    pool = multiprocessing.Pool(config['pool_size'])

    if config['enable_notifications']:
        exchange = _create_exchange(config['rabbit']['exchange_name'],
                                    'topic',
                                    durable=config['rabbit']['durable_queue'])
        routing_keys = None
        if config['rabbit'].get('routing_keys') is not None:
            routing_keys = config['rabbit']['routing_keys']

        with _create_connection(config) as conn:
            def callback(result):
                (verified, exist) = result
                if verified:
                    send_verified_notification(exist, conn, exchange,
                                               routing_keys=routing_keys)

            _run_once(config, pool, callback=callback)
    else:
        _run_once(config, pool)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=
                                     "Stacktach Instance Exists Verifier")
    parser.add_argument('--tick-time',
                        help='Time in seconds the verifier will sleep before'
                             'it will check for new exists records.',
                        default=30)
    parser.add_argument('--run-once',
                        help='Check database once and verify all returned'
                             'exists records, then stop',
                        type=bool,
                        default=False)
    parser.add_argument('--settle-time',
                        help='Time the verifier will wait for records to'
                             'settle before it will verify them.',
                        default=10)
    parser.add_argument('--settle-units',
                        help='Units for settle time',
                        default='minutes')
    parser.add_argument('--pool-size',
                        help='Number of processes created to verify records',
                        type=int,
                        default=10)
    args = parser.parse_args()
    config = {'tick_time': args.tick_time, 'settle_time': args.settle_time,
              'settle_units': args.settle_units, 'pool_size': args.pool_size}

    if args.run_once:
        run_once(config)
    else:
        run(config)
