"""Copy committed study datasets to their durable Oak sibling."""
import os
from pathlib import Path
import subprocess


def _run(command, runner):
    return runner(tuple(map(str,command)),check=True)


def _git(root,*args,runner):
    return runner(('git',*args),cwd=str(root),check=True,capture_output=True,text=True).stdout.strip()


def _identity(root,runner):
    return _git(root,'config','--file','.datalad/config','--get','datalad.dataset.id',runner=runner)


def publish_dataset(source,target,*,runner=subprocess.run):
    revision = _git(source,'rev-parse','HEAD',runner=runner)
    if (target/'.git').exists():
        if _identity(source,runner) != _identity(target,runner):
            raise RuntimeError(f'dataset identity differs: {target}')
        if _git(target,'status','--porcelain','--untracked-files=normal','--ignore-submodules=all',runner=runner):
            raise RuntimeError(f'destination has uncommitted changes: {target}')
        _run(('git','-C',target,'fetch',source,revision),runner)
        _run(('git','-C',target,'merge','--ff-only',revision),runner)
    else:
        if target.exists() and any(target.iterdir()):
            raise RuntimeError(f'nonempty unregistered destination: {target}')
        _run(('datalad','clone',source,target),runner)
    if _identity(source,runner) != _identity(target,runner) or _git(target,'rev-parse','HEAD',runner=runner) != revision:
        raise RuntimeError(f'published revision differs: {target}')
    # Get this dataset's own content, not repeated BABS input subdatasets.
    _run(('datalad','get','-d',target,target),runner)
    return revision


def publish_study(config,*,index=None,group=None,runner=subprocess.run):
    study = config.mechababs.study_dir.resolve()
    destination = config.mechababs.durable_sibling.resolve()
    if destination.is_relative_to(study) or study.is_relative_to(destination):
        raise ValueError('durable sibling must be separate from the working study')
    if _git(study,'status','--porcelain','--untracked-files=normal',runner=runner):
        raise RuntimeError('study has uncommitted changes; publication stopped')
    remote = _git(study, 'remote', 'get-url', '--push', 'oak', runner=runner)
    if Path(remote).resolve() != destination:
        raise RuntimeError('Oak remote does not match the configured durable sibling')
    head = runner(('git', 'rev-parse', '--verify', 'HEAD'), cwd=str(destination), capture_output=True, text=True)
    if head.returncode == 0 and _identity(study, runner) != _identity(destination, runner):
        raise RuntimeError('Oak study dataset identity differs')
    roots=[]
    for entry in _git(study,'ls-tree','-r','HEAD',runner=runner).splitlines():
        header,relative = entry.split('\t',1)
        path=Path(relative)
        if header.startswith('160000 ') and (relative==f'sourcedata/{config.mechababs.raw_slot}'
                or (len(path.parts)==2 and path.parts[0]=='derivatives')):
            source=study/path
            revision=header.split()[2]
            if _git(source,'rev-parse','HEAD',runner=runner)!=revision:
                raise RuntimeError(f'dataset is not registered at its current commit: {relative}')
            roots.append(relative)
    if f'sourcedata/{config.mechababs.raw_slot}' not in roots:
        raise RuntimeError('canonical BIDS subdataset is not registered')
    published={relative:publish_dataset(study/relative,destination/relative,runner=runner) for relative in roots}
    changed=False
    for relative in roots:
        key=f'submodule.{relative}.datalad-url'
        value=str(destination/relative)
        prior=runner(('git','config','-f','.gitmodules','--get',key),cwd=str(study),capture_output=True,text=True)
        if prior.stdout.strip()!=value:
            runner(('git','config','-f','.gitmodules',key,value),cwd=str(study),check=True)
            changed=True
    if changed:
        _run(('datalad','save','-d',study,'-m','Register durable study datasets','--',study/'.gitmodules'),runner)
    _run(('datalad','push','-d',study,'--to','oak','--data','anything'),runner)
    revision=_git(study,'rev-parse','HEAD',runner=runner)
    if _identity(study,runner)!=_identity(destination,runner) or _git(destination,'rev-parse','HEAD',runner=runner)!=revision:
        raise RuntimeError('Oak study did not reach the published revision')
    if _git(destination,'status','--porcelain','--untracked-files=normal',runner=runner):
        raise RuntimeError('Oak study has uncommitted changes after publication')
    if group:
        _run(('chgrp','-R','--no-dereference',group,destination),runner)
        _run(('chmod','-R','g+rX,o-rwx',destination),runner)
    if index:
        from network_fmri.records import build_index
        cache=study.parent/'.network-fmri-cache'/study.name/'published.sqlite'
        build_index(config,cache)
        target=Path(index)
        target.parent.mkdir(parents=True,exist_ok=True)
        pending=target.with_suffix('.pending')
        command=['install','-m','0640']
        if group:command+=['-g',group]
        _run((*command,cache,pending),runner)
        os.replace(pending,target)
    return {'study':str(destination),'study_commit':revision,'datasets':published}
