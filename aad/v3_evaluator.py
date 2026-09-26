"""Reference-free candidate sandbox with budget-enforced, versioned evaluation."""
import concurrent.futures
import json
import subprocess
import uuid
from pathlib import Path
from .cpp import CppEvaluator
from .io import digest,read_json,save_json
from .problem import judge
from .runner import execute,summarize

class CleanEvaluator(CppEvaluator):
    def evaluate(self,code,cases):
        folder,error=self.compile(code)
        if error:return summarize([{'name':c.name,'status':'CE','score':0,'seconds':0,'message':error} for c in cases])
        arguments=tuple(getattr(self,'arguments',()))
        driver=Path(__file__).parent/('assets/v3_param_timer.py' if arguments else 'assets/v3_timer.py')
        version='v3-clean-'+digest(driver.read_text(encoding='utf-8'))
        if arguments:version+='-args-'+digest(json.dumps(arguments))
        def one(case):
            key=digest(code+case.fingerprint+self.image+str(self.seconds)+version)
            cache=self.root/'cache'/(key+'.json')
            if cache.exists():return read_json(cache)
            name='aad-v3-'+uuid.uuid4().hex
            command=['docker','run','--rm','--name',name,'--network=none','--read-only',
                '--cap-drop=ALL','--security-opt=no-new-privileges','--pids-limit=32',
                '--memory=512m','--cpus=1','--user=65534:65534','--mount',
                f'type=bind,source={folder},target=/work,readonly','--mount',
                f'type=bind,source={driver.resolve()},target=/timer.py,readonly','-i',
                self.image,'python3','-B','/timer.py',str(self.seconds),*arguments]
            try:
                outer=execute(command,case.text,folder,30.0)
                if outer['status']=='AC':
                    try:result=json.loads(outer['stdout'])
                    except (TypeError,ValueError):result={**outer,'status':'INFRA_ERROR','stderr':'Invalid timer JSON'}
                else:result={**outer,'status':'INFRA_ERROR'}
                result['container_wall_seconds']=outer['seconds']
            finally:
                subprocess.run(['docker','rm','-f',name],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=15)
            if result['status']=='AC':result.update(judge(case.text,result['stdout']))
            else:result.update(score=0,message=result.get('stderr','')[-4000:])
            result.update(name=case.name,input_sha256=case.fingerprint)
            save_json(cache,result)
            return result
        with concurrent.futures.ThreadPoolExecutor(max_workers=self.workers) as pool:
            return summarize(list(pool.map(one,cases)))


class SanitizerEvaluator(CleanEvaluator):
    """Diagnostic builds only: never used for candidate ranking or reported scores."""
    FLAGS=['-std=c++17','-O1','-g','-fsanitize=address,undefined',
           '-fno-sanitize-recover=all','-fno-omit-frame-pointer','-D_GLIBCXX_ASSERTIONS']

    def compile(self,code):
        folder=self.root/'builds'/digest(code+' '.join(self.FLAGS)+self.image)
        folder.mkdir(parents=True,exist_ok=True)
        if (folder/'compiled.json').exists() and (folder/'solution').exists():return folder,None
        (folder/'source.cpp').write_text(code,encoding='utf-8')
        name='aad-v3-debug-build-'+uuid.uuid4().hex
        command=['docker','run','--rm','--name',name,'--network=none','--memory=1g','--cpus=2',
            '--pids-limit=64','--cap-drop=ALL','--security-opt=no-new-privileges','--read-only',
            '--tmpfs','/tmp:rw,size=256m','--mount',f'type=bind,source={folder},target=/work',
            self.image,'g++',*self.FLAGS,'/work/source.cpp','-o','/work/solution']
        try:
            result=subprocess.run(command,capture_output=True,text=True,timeout=60)
        except subprocess.TimeoutExpired:
            return folder,'Diagnostic compiler exceeded 60 seconds'
        finally:
            subprocess.run(['docker','rm','-f',name],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=15)
        if result.returncode:return folder,result.stderr[-8000:]
        save_json(folder/'compiled.json',{'image':self.image,'flags':self.FLAGS,'diagnostic_only':True})
        return folder,None
