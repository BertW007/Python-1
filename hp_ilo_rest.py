#!/usr/bin/python

from pydoc import render_doc
import requests
import yaml
import os
try:
  import json
except:
  import simplejson as json
import re
import subprocess
import sys
import MySQLdb

try:
  requests.packages.urllib3.disable_warnings()
except:
  pass

verbose = False

username = 'Administrator'
password = 'secret'
verify = False
timeout = 30.05

default_bioscfg = 'bios/bios-settings-desired.yaml'



class HPiLO:
  """
  This class reads a list of machines from a YAML file, then performs
  operations on those machines via the HP ilO REST API. Defaults are
  set above, but they should probably be parameterized as CLI args or
  a config file.
  """
  def __init__(
          self, host='', ids=[], bios_cfg=''):
    self.hosts = {}
    if host:
        hosturi = "https://" + host
        self.hosts[hosturi] = {
                'authdata': {
                    'UserName': username, 'Password': password},
                'headers': {
                    'Content-type': 'application/json',
                    'Accept': 'text/plain'},
                'verify': verify,
                'sessionpath': None,
                'bioscfgpath': default_bioscfg}
        if bios_cfg:
            bios_cfg = "bios/" + bios_cfg
            self.hosts[hosturi]['bioscfgpath'] = bios_cfg
    db = db_connect(db_info_key)
    cur = db.cursor()
    if ids:
        for host_id in ids:
            select_cmd = 'SELECT lom_ip, bios_config FROM %s WHERE id = %d' % (db_table, host_id)
            if verbose:
                print select_cmd
            cur.execute(select_cmd)
            hostinfo = cur.fetchone()
            if hostinfo:
                host = hostinfo[0]
                bios_cfg = hostinfo[1]
                hosturi = "https://" + host
                self.hosts[hosturi] = {
                        'authdata': {
                            'UserName': username, 'Password': password},
                        'headers': {
                            'Content-type': 'application/json',
                            'Accept': 'text/plain'},
                        'verify': verify,
                        'sessionpath': None,
                        'bioscfgpath': default_bioscfg}
                if bios_cfg:
                    bios_cfg = "bios/" + bios_cfg
                    self.hosts[hosturi]['bioscfgpath'] = bios_cfg
    db.close()

  def iLO_operation(self, operation, uripath, targets=None,
                    requireLogin=True, data=None):
    """
    Perform a rest action and return the results. Operation defines
    what HTTP method will be used. Login operations are a special
    case as we need to be logged in to perform other operations.
    The "uripath" variable describes the path beyond the baseURL
    to call the method on. Targets is a list of URLs which should
    exist in the hosts dictionary, which was read from the YAML
    configuration file.
    """
    operationDict = {
        "get": requests.get,
        "post": requests.post,
        "login": requests.post,
        "delete": requests.delete,
        "patch": requests.patch,
        "put": requests.put}
    ret = {}
    if not targets:
      targets = self.hosts.keys()

    # Sort them so we can predict their order better while running on large batches
    targets.sort()

    total_targets = len(targets)
    if verbose:
        print '%s: Total targets: %s' % (operation, total_targets)

    count = 0
    for hosturi in targets:
      count += 1
      if verbose:
          print 'Target: %s  [%s of %s]' % (hosturi, count, total_targets)

      if not self.hosts[hosturi]['sessionpath'] and requireLogin \
              and operation != "login":
            self.login(targets=[hosturi])
      try:
        res = operationDict[operation](
            hosturi + uripath,
            verify=self.hosts[hosturi]['verify'],
            headers=self.hosts[hosturi]['headers'],
            data=data,
            timeout=timeout)
        ret[hosturi + uripath] = {
            operation: {'text': yaml.safe_load(res.text) or {}}}
        ret[hosturi + uripath][operation]['status code'] = res.status_code
        ret[hosturi + uripath][operation]['headers'] = res.headers
      except Exception as ex:
        sys.stderr.write(operation + " operation failed on " + hosturi + uripath + \
            ":\n " + type(ex).__name__ + ":  " + str(ex) + '\n')
    return ret

  def login(self, targets=None):
    """
    Log in to one or more iLOs by calling iLO_operation. See
    iLO_operation method for description of targets parameter.
    """
    ret = {}
    if not targets:
      targets = self.hosts.keys()
    for hosturi in targets:
      uripath = "/rest/v1/Sessions"
      operation = "login"
      data = json.dumps(self.hosts[hosturi]['authdata'])
      ret.update(self.iLO_operation(
          operation=operation,
          targets=[hosturi],
          uripath=uripath,
          data=data))
      try:
        self.hosts[hosturi]['headers']['x-auth-token'] = \
            ret[hosturi + uripath][operation]['headers']['x-auth-token']
        self.hosts[hosturi]['sessionpath'] = \
            ret[hosturi + uripath][operation]['headers']['Location']\
            .replace(hosturi, "")
      except Exception as ex:
         sys.stderr.write("Login operation failed on " + hosturi + uripath + \
            ":\n " + type(ex).__name__ + ":  " + str(ex) + '\n')
    return ret

  def logout(self, targets=None):
    """
    Log out of one or more iLOs by calling iLO_operation. See
    iLO_operation method for description of targets parameter.
    """
    ret = {}
    if not targets:
      targets = self.hosts.keys()
    for hosturi in targets:
      uripath = self.hosts[hosturi]['sessionpath']
      if uripath:
        ret.update(self.iLO_operation(
            operation="delete",
            targets=[hosturi],
            uripath=uripath,
            requireLogin=False))
    return ret

  def ResetBIOS(self, targets=None):
    """
    Reset BIOS settings and boot order to Defaults. See
    iLO_operation method for description of targets parameter.
    """
    data = json.dumps({
        'RestoreManufacturingDefaults': 'yes',
        'BaseConfig': 'default'})
    return self.iLO_operation(
        operation="patch",
        uripath="/rest/v1/systems/1/bios/Settings",
        targets=targets, data=data)

  def GetBootOrder(self, targets=None):
    """
    Get Boot order. See iLO_operation method for description
    of targets parameter.
    """
    return self.iLO_operation(
        operation="get",
        uripath="/rest/v1/systems/1/bios/Boot/Settings",
        targets=targets)

  def GetBIOS(self, targets=None):
    """
    Get BIOS settings. See iLO_operation method for description
    of targets parameter.
    """
    return self.iLO_operation(
        operation="get",
        uripath="/rest/v1/systems/1/bios/Settings",
        targets=targets)

  def SetBIOS(self, targets=None):
    """
    Update BIOS settings on specified device. Settings are defined by
    a YAML file whose path is specified in the main config file.
    See iLO_operation method for description of targets parameter.
    """
    ret = {}
    if not targets:
      targets = self.hosts.keys()
    for hosturi in targets:
      uripath = "/rest/v1/systems/1/bios/Settings"
      try:
        with open(self.hosts[hosturi]['bioscfgpath'], 'r') as f:
          d = yaml.safe_load(f)
        data = json.dumps(d)
        ret.update(self.iLO_operation(
            operation="patch",
            uripath=uripath,
            targets=[hosturi],
            data=data))
      except Exception as ex:
        sys.stderr.write("set operation failed on " + hosturi + uripath \
            + ":\n " + type(ex).__name__ + ":  " + str(ex) + '\n')
    return ret

  def ResetPower(self, targets=None):
    """
    Reset power. See iLO_operation method for description
    of targets parameter.
    """
    data = json.dumps({
        'Action': 'Reset',
        'ResetType': 'ForceRestart'})
    return self.iLO_operation(
        operation="post",
        uripath="/rest/v1/Systems/1",
        targets=targets, data=data)

  def PowerOff(self, targets=None):
    """
    Force power off. See iLO_operation method for description
    of targets parameter.
    """
    data = json.dumps({
        'Action': 'Reset',
        'ResetType': 'ForceOff'})
    return self.iLO_operation(
        operation="post",
        uripath="/rest/v1/Systems/1",
        targets=targets, data=data)

  def PowerOn(self, targets=None):
    """
    Power on. See iLO_operation method for description
    of targets parameter.
    """
    data = json.dumps({
        'Action': 'Reset',
        'ResetType': 'On'})
    return self.iLO_operation(
        operation="post",
        uripath="/rest/v1/Systems/1",
        targets=targets, data=data)

  def OneTimeBoot(self, bootdev="pxe10g", targets=None):
    """
    Use REST API to set one time boot to PXE.
    Should do better error handling
    bootdev=pxe: Force PXE boot
    bootdev=pxe10g: Force PXE boot on 10GbE FlexLOM
    """
    bootdict = {
        'pxe10g': 'Pxe',
        'pxe': 'Pxe'}
    uefidict = {
        'pxe': 'NIC.LOM.1.1.IPv4',
        'pxe10g': 'NIC.FlexLOM.1.1.IPv4'}
    try:
      boottarget = bootdict[bootdev]
      uefitarget = uefidict[bootdev]
      data = json.dumps({'Boot': {
        'BootSourceOverrideTarget': boottarget,
        'UefiTargetBootSourceOverride': uefitarget,
        }}) #'BootSourceOverrideEnabled': True,
      return self.iLO_operation(
          operation="patch",
          uripath="/rest/v1/systems/1",
          targets=targets, data=data)
    except:
      sys.stderr.write("Invalid boot target: " + str(bootdev) + '\n')



def help():
  print render_doc(HPiLO, "Help on %s")


def main():
    help()


if __name__ == "__main__":
  main()
#
