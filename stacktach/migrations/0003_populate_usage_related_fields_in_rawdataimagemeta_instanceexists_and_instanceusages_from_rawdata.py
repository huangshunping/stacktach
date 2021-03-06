# -*- coding: utf-8 -*-
import copy
from south.v2 import DataMigration
from stacktach.notification import Notification
from stacktach.views import NOTIFICATIONS

try:
    import ujson as json
except ImportError:
    try:
        import simplejson as json
    except ImportError:
        import json

USAGE_EVENTS = [
    'compute.instance.create.start',
    'compute.instance.create.end',
    'compute.instance.rebuild.start',
    'compute.instance.rebuild.end',
    'compute.instance.resize.prep.start',
    'compute.instance.resize.prep.end',
    'compute.instance.resize.revert.start',
    'compute.instance.resize.revert.end',
    'compute.instance.finish_resize.end',
    'compute.instance.delete.end',
    'compute.instance.exists']

USAGE_EVENTS_EXCEPT_EXISTS = copy.deepcopy(USAGE_EVENTS)
USAGE_EVENTS_EXCEPT_EXISTS.remove('compute.instance.exists')


class Migration(DataMigration):

    def _find_latest_usage_related_raw_id_for_request_id(self, orm, request_id):
        rawdata = orm.RawData.objects.filter(
            request_id=request_id,
            event__in=USAGE_EVENTS_EXCEPT_EXISTS).order_by('id')[:1].values('id')
        if rawdata.count() > 0:
            return rawdata[0]['id']
        return None

    def _notification(self, json_message):
        json_dict = json.loads(json_message)
        routing_key = json_dict[0]
        body = json_dict[1]
        notification = NOTIFICATIONS[routing_key](body)
        return notification

    def forwards(self, orm):
        # Note: Don't use "from appname.models import ModelName".
        # Use orm.ModelName to refer to models in this application,
        # and orm['appname.ModelName'] for models in other applications.
        print "Started inserting records in RawDataImageMeta"
        rawdata_all = orm.RawData.objects.filter(event__in=USAGE_EVENTS).values('json', 'id')
        for rawdata in rawdata_all:
            notification = self._notification(rawdata['json'])
            orm.RawDataImageMeta.objects.create(
                raw_id=rawdata['id'],
                os_architecture=notification.os_architecture,
                os_distro=notification.os_distro,
                os_version=notification.os_version,
                rax_options=notification.rax_options)
        print "Inserted %s records in RawDataImageMeta" % rawdata_all.count()

        print "\nStarted updating records in InstanceExists"
        exists = orm.InstanceExists.objects.values('raw_id')
        exists_update_count = 0
        for exist in exists:
            image_metadata = orm.RawDataImageMeta.objects.filter(raw_id=exist['raw_id'])
            if image_metadata.count() == 0:
                print "RawDataImageMeta not found for InstanceExists with raw_id %s" % exist['raw_id']
                continue
            orm.InstanceExists.objects.filter(
                raw_id=exist['raw_id']).update(
                    os_architecture=image_metadata[0].os_architecture,
                    os_distro=image_metadata[0].os_distro,
                    os_version=image_metadata[0].os_version,
                    rax_options=image_metadata[0].rax_options)
            exists_update_count += 1
        print "Updated %s records in InstanceExists" % exists_update_count

        print "\nStarted updating records in InstacnceUsages"
        usages = orm.InstanceUsage.objects.all().values('request_id')
        usages_update_count = 0
        for usage in usages:
            raw_id = self._find_latest_usage_related_raw_id_for_request_id(orm, usage['request_id'])
            if not raw_id:
                print "No Rawdata entry found for a usage related event with request_id %s" % usage['request_id']
                continue
            image_metadata = orm.RawDataImageMeta.objects.filter(raw_id=raw_id)[0]
            orm.InstanceUsage.objects.filter(
                request_id=usage['request_id']).update(
                    os_architecture=image_metadata.os_architecture,
                    os_distro=image_metadata.os_distro,
                    os_version=image_metadata.os_version,
                    rax_options=image_metadata.rax_options)
            usages_update_count += 1
        print "Updated %s records in InstanceUsages" % usages_update_count

    def backwards(self, orm):
        raise RuntimeError("Cannot reverse this migration.")


    models = {
        u'stacktach.deployment': {
            'Meta': {'object_name': 'Deployment'},
            u'id': ('django.db.models.fields.AutoField', [], {'primary_key': 'True'}),
            'name': ('django.db.models.fields.CharField', [], {'max_length': '50'})
        },
        u'stacktach.instancedeletes': {
            'Meta': {'object_name': 'InstanceDeletes'},
            'deleted_at': ('django.db.models.fields.DecimalField', [], {'null': 'True', 'max_digits': '20', 'decimal_places': '6', 'db_index': 'True'}),
            u'id': ('django.db.models.fields.AutoField', [], {'primary_key': 'True'}),
            'instance': ('django.db.models.fields.CharField', [], {'db_index': 'True', 'max_length': '50', 'null': 'True', 'blank': 'True'}),
            'launched_at': ('django.db.models.fields.DecimalField', [], {'null': 'True', 'max_digits': '20', 'decimal_places': '6', 'db_index': 'True'}),
            'raw': ('django.db.models.fields.related.ForeignKey', [], {'to': u"orm['stacktach.RawData']", 'null': 'True'})
        },
        u'stacktach.instanceexists': {
            'Meta': {'object_name': 'InstanceExists'},
            'audit_period_beginning': ('django.db.models.fields.DecimalField', [], {'null': 'True', 'max_digits': '20', 'decimal_places': '6', 'db_index': 'True'}),
            'audit_period_ending': ('django.db.models.fields.DecimalField', [], {'null': 'True', 'max_digits': '20', 'decimal_places': '6', 'db_index': 'True'}),
            'delete': ('django.db.models.fields.related.ForeignKey', [], {'related_name': "'+'", 'null': 'True', 'to': u"orm['stacktach.InstanceDeletes']"}),
            'deleted_at': ('django.db.models.fields.DecimalField', [], {'null': 'True', 'max_digits': '20', 'decimal_places': '6', 'db_index': 'True'}),
            'fail_reason': ('django.db.models.fields.CharField', [], {'db_index': 'True', 'max_length': '300', 'null': 'True', 'blank': 'True'}),
            u'id': ('django.db.models.fields.AutoField', [], {'primary_key': 'True'}),
            'instance': ('django.db.models.fields.CharField', [], {'db_index': 'True', 'max_length': '50', 'null': 'True', 'blank': 'True'}),
            'instance_type_id': ('django.db.models.fields.CharField', [], {'db_index': 'True', 'max_length': '50', 'null': 'True', 'blank': 'True'}),
            'launched_at': ('django.db.models.fields.DecimalField', [], {'null': 'True', 'max_digits': '20', 'decimal_places': '6', 'db_index': 'True'}),
            'message_id': ('django.db.models.fields.CharField', [], {'db_index': 'True', 'max_length': '50', 'null': 'True', 'blank': 'True'}),
            'os_architecture': ('django.db.models.fields.TextField', [], {'null': 'True', 'blank': 'True'}),
            'os_distro': ('django.db.models.fields.TextField', [], {'null': 'True', 'blank': 'True'}),
            'os_version': ('django.db.models.fields.TextField', [], {'null': 'True', 'blank': 'True'}),
            'raw': ('django.db.models.fields.related.ForeignKey', [], {'related_name': "'+'", 'null': 'True', 'to': u"orm['stacktach.RawData']"}),
            'rax_options': ('django.db.models.fields.TextField', [], {'null': 'True', 'blank': 'True'}),
            'send_status': ('django.db.models.fields.IntegerField', [], {'default': '0', 'null': 'True', 'db_index': 'True'}),
            'status': ('django.db.models.fields.CharField', [], {'default': "'pending'", 'max_length': '50', 'db_index': 'True'}),
            'tenant': ('django.db.models.fields.CharField', [], {'db_index': 'True', 'max_length': '50', 'null': 'True', 'blank': 'True'}),
            'usage': ('django.db.models.fields.related.ForeignKey', [], {'related_name': "'+'", 'null': 'True', 'to': u"orm['stacktach.InstanceUsage']"})
        },
        u'stacktach.instanceusage': {
            'Meta': {'object_name': 'InstanceUsage'},
            u'id': ('django.db.models.fields.AutoField', [], {'primary_key': 'True'}),
            'instance': ('django.db.models.fields.CharField', [], {'db_index': 'True', 'max_length': '50', 'null': 'True', 'blank': 'True'}),
            'instance_type_id': ('django.db.models.fields.CharField', [], {'db_index': 'True', 'max_length': '50', 'null': 'True', 'blank': 'True'}),
            'launched_at': ('django.db.models.fields.DecimalField', [], {'null': 'True', 'max_digits': '20', 'decimal_places': '6', 'db_index': 'True'}),
            'os_architecture': ('django.db.models.fields.TextField', [], {'null': 'True', 'blank': 'True'}),
            'os_distro': ('django.db.models.fields.TextField', [], {'null': 'True', 'blank': 'True'}),
            'os_version': ('django.db.models.fields.TextField', [], {'null': 'True', 'blank': 'True'}),
            'rax_options': ('django.db.models.fields.TextField', [], {'null': 'True', 'blank': 'True'}),
            'request_id': ('django.db.models.fields.CharField', [], {'db_index': 'True', 'max_length': '50', 'null': 'True', 'blank': 'True'}),
            'tenant': ('django.db.models.fields.CharField', [], {'db_index': 'True', 'max_length': '50', 'null': 'True', 'blank': 'True'})
        },
        u'stacktach.jsonreport': {
            'Meta': {'object_name': 'JsonReport'},
            'created': ('django.db.models.fields.DecimalField', [], {'max_digits': '20', 'decimal_places': '6', 'db_index': 'True'}),
            u'id': ('django.db.models.fields.AutoField', [], {'primary_key': 'True'}),
            'json': ('django.db.models.fields.TextField', [], {}),
            'name': ('django.db.models.fields.CharField', [], {'max_length': '50', 'db_index': 'True'}),
            'period_end': ('django.db.models.fields.DateTimeField', [], {'db_index': 'True'}),
            'period_start': ('django.db.models.fields.DateTimeField', [], {'db_index': 'True'}),
            'version': ('django.db.models.fields.IntegerField', [], {'default': '1'})
        },
        u'stacktach.lifecycle': {
            'Meta': {'object_name': 'Lifecycle'},
            u'id': ('django.db.models.fields.AutoField', [], {'primary_key': 'True'}),
            'instance': ('django.db.models.fields.CharField', [], {'db_index': 'True', 'max_length': '50', 'null': 'True', 'blank': 'True'}),
            'last_raw': ('django.db.models.fields.related.ForeignKey', [], {'to': u"orm['stacktach.RawData']", 'null': 'True'}),
            'last_state': ('django.db.models.fields.CharField', [], {'db_index': 'True', 'max_length': '50', 'null': 'True', 'blank': 'True'}),
            'last_task_state': ('django.db.models.fields.CharField', [], {'db_index': 'True', 'max_length': '50', 'null': 'True', 'blank': 'True'})
        },
        u'stacktach.rawdata': {
            'Meta': {'object_name': 'RawData'},
            'deployment': ('django.db.models.fields.related.ForeignKey', [], {'to': u"orm['stacktach.Deployment']"}),
            'event': ('django.db.models.fields.CharField', [], {'db_index': 'True', 'max_length': '50', 'null': 'True', 'blank': 'True'}),
            'host': ('django.db.models.fields.CharField', [], {'db_index': 'True', 'max_length': '100', 'null': 'True', 'blank': 'True'}),
            u'id': ('django.db.models.fields.AutoField', [], {'primary_key': 'True'}),
            'image_type': ('django.db.models.fields.IntegerField', [], {'default': '0', 'null': 'True', 'db_index': 'True'}),
            'instance': ('django.db.models.fields.CharField', [], {'db_index': 'True', 'max_length': '50', 'null': 'True', 'blank': 'True'}),
            'json': ('django.db.models.fields.TextField', [], {}),
            'old_state': ('django.db.models.fields.CharField', [], {'db_index': 'True', 'max_length': '20', 'null': 'True', 'blank': 'True'}),
            'old_task': ('django.db.models.fields.CharField', [], {'db_index': 'True', 'max_length': '30', 'null': 'True', 'blank': 'True'}),
            'publisher': ('django.db.models.fields.CharField', [], {'db_index': 'True', 'max_length': '100', 'null': 'True', 'blank': 'True'}),
            'request_id': ('django.db.models.fields.CharField', [], {'db_index': 'True', 'max_length': '50', 'null': 'True', 'blank': 'True'}),
            'routing_key': ('django.db.models.fields.CharField', [], {'db_index': 'True', 'max_length': '50', 'null': 'True', 'blank': 'True'}),
            'service': ('django.db.models.fields.CharField', [], {'db_index': 'True', 'max_length': '50', 'null': 'True', 'blank': 'True'}),
            'state': ('django.db.models.fields.CharField', [], {'db_index': 'True', 'max_length': '20', 'null': 'True', 'blank': 'True'}),
            'task': ('django.db.models.fields.CharField', [], {'db_index': 'True', 'max_length': '30', 'null': 'True', 'blank': 'True'}),
            'tenant': ('django.db.models.fields.CharField', [], {'db_index': 'True', 'max_length': '50', 'null': 'True', 'blank': 'True'}),
            'when': ('django.db.models.fields.DecimalField', [], {'max_digits': '20', 'decimal_places': '6', 'db_index': 'True'})
        },
        u'stacktach.rawdataimagemeta': {
            'Meta': {'object_name': 'RawDataImageMeta'},
            u'id': ('django.db.models.fields.AutoField', [], {'primary_key': 'True'}),
            'os_architecture': ('django.db.models.fields.TextField', [], {'null': 'True', 'blank': 'True'}),
            'os_distro': ('django.db.models.fields.TextField', [], {'null': 'True', 'blank': 'True'}),
            'os_version': ('django.db.models.fields.TextField', [], {'null': 'True', 'blank': 'True'}),
            'raw': ('django.db.models.fields.related.ForeignKey', [], {'to': u"orm['stacktach.RawData']"}),
            'rax_options': ('django.db.models.fields.TextField', [], {'null': 'True', 'blank': 'True'})
        },
        u'stacktach.requesttracker': {
            'Meta': {'object_name': 'RequestTracker'},
            'completed': ('django.db.models.fields.BooleanField', [], {'default': 'False', 'db_index': 'True'}),
            'duration': ('django.db.models.fields.DecimalField', [], {'max_digits': '20', 'decimal_places': '6', 'db_index': 'True'}),
            u'id': ('django.db.models.fields.AutoField', [], {'primary_key': 'True'}),
            'last_timing': ('django.db.models.fields.related.ForeignKey', [], {'to': u"orm['stacktach.Timing']", 'null': 'True'}),
            'lifecycle': ('django.db.models.fields.related.ForeignKey', [], {'to': u"orm['stacktach.Lifecycle']"}),
            'request_id': ('django.db.models.fields.CharField', [], {'max_length': '50', 'db_index': 'True'}),
            'start': ('django.db.models.fields.DecimalField', [], {'max_digits': '20', 'decimal_places': '6', 'db_index': 'True'})
        },
        u'stacktach.timing': {
            'Meta': {'object_name': 'Timing'},
            'diff': ('django.db.models.fields.DecimalField', [], {'null': 'True', 'max_digits': '20', 'decimal_places': '6', 'db_index': 'True'}),
            'end_raw': ('django.db.models.fields.related.ForeignKey', [], {'related_name': "'+'", 'null': 'True', 'to': u"orm['stacktach.RawData']"}),
            'end_when': ('django.db.models.fields.DecimalField', [], {'null': 'True', 'max_digits': '20', 'decimal_places': '6'}),
            u'id': ('django.db.models.fields.AutoField', [], {'primary_key': 'True'}),
            'lifecycle': ('django.db.models.fields.related.ForeignKey', [], {'to': u"orm['stacktach.Lifecycle']"}),
            'name': ('django.db.models.fields.CharField', [], {'max_length': '50', 'db_index': 'True'}),
            'start_raw': ('django.db.models.fields.related.ForeignKey', [], {'related_name': "'+'", 'null': 'True', 'to': u"orm['stacktach.RawData']"}),
            'start_when': ('django.db.models.fields.DecimalField', [], {'null': 'True', 'max_digits': '20', 'decimal_places': '6'})
        }
    }

    complete_apps = ['stacktach']
    symmetrical = True
