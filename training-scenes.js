/* Quiet training backgrounds. Redrawn source images are kept unmodified on disk.
   Only masked sky/water texture coordinates move; image luminance, terrain and
   camera position never follow EEG, music, or time. No sound is created here. */
(() => {
  'use strict';
  const MODES=Object.freeze([
    {id:'lake-trees',label:'湖岸 · 舒缓云水'},
    {id:'lake-house',label:'湖屋 · 舒缓云水'},
    {id:'rings-still',label:'完整三圈 · 静止'},
    {id:'rings-feedback',label:'三圈 · 动态反馈（演示）'}
  ]);
  const normalize=value=>MODES.some(item=>item.id===value)?value:'lake-trees';
  const definitions={
    'lake-trees':{
      src:'assets/visuals/training-lake-trees.png',
      // Lower mask edges remain above the mountains, then feather inward.
      sky:[[0,0],[1,0],[1,.25],[.94,.25],[.88,.25],[.82,.254],[.77,.259],[.74,.29],[.70,.305],[.64,.31],[.58,.321],[.54,.325],[.49,.325],[.46,.333],[.40,.337],[.36,.342],[.30,.321],[.24,.305],[.20,.302],[.17,.295],[.14,.293],[.10,.29],[.077,.282],[.047,.282],[.026,.287],[0,.288]],
      // Water begins below all reeds, trees and shoreline.
      water:[[0,.789],[1,.789],[1,1],[0,1]]
    },
    'lake-house':{
      src:'assets/visuals/training-lake-house.png',
      sky:[[0,0],[1,0],[1,.18],[.89,.18],[.87,.43],[.80,.45],[.73,.457],[.70,.452],[.69,.410],[.40,.410],[.37,.445],[.32,.445],[.24,.420],[.18,.395],[.12,.375],[.06,.360],[0,.360]],
      // Keep the house, foreground reeds and centre-left bushes motionless.
      water:[[.410,.710],[.710,.710],[.720,.625],[.820,.625],[.815,.780],[.840,.860],[.820,1],[.430,1],[.430,.920],[.435,.835]]
    }
  };
  const entries=new Map();
  const reducedMotion=window.matchMedia?.('(prefers-reduced-motion: reduce)');
  const lowPower=(navigator.deviceMemory>0 && navigator.deviceMemory<=4) ||
    (navigator.hardwareConcurrency>0 && navigator.hardwareConcurrency<=4);
  function maskImage(definition,width,height){
    const mask=document.createElement('canvas');mask.width=width;mask.height=height;
    const maskCtx=mask.getContext('2d');
    maskCtx.fillStyle='#000';maskCtx.fillRect(0,0,width,height);
    const layer=document.createElement('canvas');layer.width=width;layer.height=height;
    const layerCtx=layer.getContext('2d');
    for(const [region,color] of [['sky','#f00'],['water','#0f0']]){
      layerCtx.clearRect(0,0,width,height);layerCtx.fillStyle=color;
      layerCtx.beginPath();definition[region].forEach(([x,y],index)=>{
        if(index)layerCtx.lineTo(x*width,y*height);else layerCtx.moveTo(x*width,y*height);
      });layerCtx.closePath();layerCtx.fill();
      // Blur inside the conservative region only. The outer terrain never
      // receives motion; the feather removes a visible border in the sky.
      // Clip the blurred result to its original polygon. Blurring alone would
      // leak movement into the neighbouring mountain, roof or foreground plants.
      maskCtx.save();maskCtx.beginPath();definition[region].forEach(([x,y],index)=>{
        if(index)maskCtx.lineTo(x*width,y*height);else maskCtx.moveTo(x*width,y*height);
      });maskCtx.closePath();maskCtx.clip();
      maskCtx.globalCompositeOperation='lighter';maskCtx.filter=`blur(${Math.max(8,Math.round(width*.006))}px)`;
      maskCtx.drawImage(layer,0,0);maskCtx.restore();
    }
    return mask;
  }
  function createRenderer(image,definition){
    const surface=document.createElement('canvas');
    const gl=surface.getContext('webgl',{alpha:false,antialias:false,preserveDrawingBuffer:true,powerPreference:'low-power'});
    if(!gl)return null;
    // Keep these redraws at their native resolution. Larger future assets are
    // limited by the GPU and a device-sensitive pixel budget rather than 1440px.
    const textureLimit=gl.getParameter(gl.MAX_TEXTURE_SIZE);
    const viewportLimit=gl.getParameter(gl.MAX_VIEWPORT_DIMS);
    const pixelBudget=lowPower?1600000:4000000;
    const resolutionScale=Math.min(1,textureLimit/image.naturalWidth,textureLimit/image.naturalHeight,
      viewportLimit[0]/image.naturalWidth,viewportLimit[1]/image.naturalHeight,
      Math.sqrt(pixelBudget/(image.naturalWidth*image.naturalHeight)));
    surface.width=Math.max(1,Math.round(image.naturalWidth*resolutionScale));
    surface.height=Math.max(1,Math.round(image.naturalHeight*resolutionScale));
    function shader(type,source){
      const result=gl.createShader(type);gl.shaderSource(result,source);gl.compileShader(result);
      if(!gl.getShaderParameter(result,gl.COMPILE_STATUS))throw new Error('Scene shader unavailable');
      return result;
    }
    try{
      const program=gl.createProgram();
      gl.attachShader(program,shader(gl.VERTEX_SHADER,'attribute vec2 position; varying vec2 uv; void main(){ uv=vec2((position.x+1.0)*0.5,(1.0-position.y)*0.5); gl_Position=vec4(position,0.0,1.0); }'));
      gl.attachShader(program,shader(gl.FRAGMENT_SHADER,`
        #ifdef GL_FRAGMENT_PRECISION_HIGH
          precision highp float;
        #else
          precision mediump float;
        #endif
        varying vec2 uv;
        uniform sampler2D photograph;
        uniform sampler2D regions;
        uniform float seconds;
        void main(){
          // Threshold the clipped blur to feather inward to zero at its edge.
          vec2 area=smoothstep(vec2(.5),vec2(.98),texture2D(regions,uv).rg);
          float edgeFade=smoothstep(0.0,.04,uv.x)*smoothstep(0.0,.04,1.0-uv.x);
          // Approximately 3 source pixels/second at 1606px wide at the start;
          // a slow 84-second arc makes cloud drift visible without a camera move.
          vec2 cloud=vec2(sin(seconds*.075)*.024,sin(seconds*.05)*.0012)*edgeFade;
          // Moving ripples repeat over 5-7 seconds, with no sparkle, flashes or
          // brightness changes. Their strength rises gently toward the viewer.
          float depth=.35+.65*smoothstep(.60,1.0,uv.y);
          vec2 water=vec2(sin(uv.y*150.0-seconds*1.15+uv.x*3.0)*.0042,
            sin(uv.y*105.0+seconds*.90+uv.x*5.0)*.0018)*depth*edgeFade;
          vec2 offset=cloud*area.r+water*area.g;
          // Both the destination and sampled point must stay in their region.
          // This also guards sloping ridgelines from a horizontal cloud shift.
          vec2 sourceArea=smoothstep(vec2(.5),vec2(.98),
            texture2D(regions,clamp(uv+offset,vec2(0.0),vec2(1.0))).rg);
          offset=cloud*min(area.r,sourceArea.r)+water*min(area.g,sourceArea.g);
          gl_FragColor=texture2D(photograph,clamp(uv+offset,vec2(0.0),vec2(1.0)));
        }
      `));
      gl.linkProgram(program);
      if(!gl.getProgramParameter(program,gl.LINK_STATUS))throw new Error('Scene program unavailable');
      gl.useProgram(program);
      const buffer=gl.createBuffer();gl.bindBuffer(gl.ARRAY_BUFFER,buffer);
      gl.bufferData(gl.ARRAY_BUFFER,new Float32Array([-1,-1,3,-1,-1,3]),gl.STATIC_DRAW);
      const position=gl.getAttribLocation(program,'position');gl.enableVertexAttribArray(position);gl.vertexAttribPointer(position,2,gl.FLOAT,false,0,0);
      function texture(source,unit,name){
        gl.activeTexture(gl.TEXTURE0+unit);gl.bindTexture(gl.TEXTURE_2D,gl.createTexture());
        gl.texParameteri(gl.TEXTURE_2D,gl.TEXTURE_MIN_FILTER,gl.LINEAR);gl.texParameteri(gl.TEXTURE_2D,gl.TEXTURE_MAG_FILTER,gl.LINEAR);
        gl.texParameteri(gl.TEXTURE_2D,gl.TEXTURE_WRAP_S,gl.CLAMP_TO_EDGE);gl.texParameteri(gl.TEXTURE_2D,gl.TEXTURE_WRAP_T,gl.CLAMP_TO_EDGE);
        gl.texImage2D(gl.TEXTURE_2D,0,gl.RGB,gl.RGB,gl.UNSIGNED_BYTE,source);
        gl.uniform1i(gl.getUniformLocation(program,name),unit);
      }
      // Oversized source images must also be reduced before texture upload.
      let photograph=image;
      if(resolutionScale<1){
        photograph=document.createElement('canvas');photograph.width=surface.width;photograph.height=surface.height;
        photograph.getContext('2d').drawImage(image,0,0,photograph.width,photograph.height);
      }
      texture(photograph,0,'photograph');texture(maskImage(definition,surface.width,surface.height),1,'regions');
      const seconds=gl.getUniformLocation(program,'seconds');
      let renderedTime=null,nextFrameTime=null;
      const frameInterval=1/(lowPower?24:30);
      surface.addEventListener('webglcontextlost',event=>event.preventDefault());
      return {surface,render(time){
        if(gl.isContextLost())return false;
        if(time===renderedTime)return true;
        if(renderedTime!==null && time<renderedTime)nextFrameTime=null;
        if(nextFrameTime!==null && time+1e-7<nextFrameTime)return true;
        gl.viewport(0,0,surface.width,surface.height);gl.uniform1f(seconds,time);
        gl.drawArrays(gl.TRIANGLES,0,3);renderedTime=time;
        // Keep a fixed cadence and carry any fractional interval forward. A
        // 30fps caller can then supply 24fps rather than dropping to 15fps.
        nextFrameTime=nextFrameTime===null?time+frameInterval:
          nextFrameTime+(Math.floor((time-nextFrameTime+1e-7)/frameInterval)+1)*frameInterval;
        return true;
      }};
    }catch(error){return null;}
  }
  function load(mode){
    if(!definitions[mode] || entries.has(mode))return entries.get(mode);
    const image=new Image(),entry={image,loaded:false,error:false,renderer:null};entries.set(mode,entry);
    image.onload=()=>{entry.loaded=true;entry.renderer=createRenderer(image,definitions[mode]);};
    image.onerror=()=>{entry.error=true;};image.src=definitions[mode].src;
    return entry;
  }
  let timeline=0,lastTime=null,currentMode='lake-trees';
  function draw(ctx,width,height,{mode='lake-trees',now=performance.now(),motion=true}={}){
    mode=normalize(mode);if(!definitions[mode])return false;
    const elapsed=lastTime===null?0:Math.max(0,Math.min(.1,(now-lastTime)/1000));lastTime=now;
    // A paused phase freezes the current frame without returning to frame zero.
    if(motion && !reducedMotion?.matches)timeline+=elapsed;
    currentMode=mode;
    const entry=load(mode);
    if(!entry?.loaded)return false;
    const drawn=entry.renderer?.render(timeline),source=drawn?entry.renderer.surface:entry.image;
    const sourceWidth=source.width || source.naturalWidth,sourceHeight=source.height || source.naturalHeight;
    const scale=Math.max(width/sourceWidth,height/sourceHeight);
    ctx.drawImage(source,(width-sourceWidth*scale)/2,(height-sourceHeight*scale)/2,sourceWidth*scale,sourceHeight*scale);
    return true;
  }
  // Preloading avoids a picture appearing partway through the spoken start cue.
  load('lake-trees');load('lake-house');
  window.TrainingScenes=Object.freeze({modes:MODES,normalize,isPhoto:mode=>!!definitions[normalize(mode)],draw,
    diagnostics:()=>({mode:currentMode,time:timeline,assets:Object.fromEntries([...entries].map(([key,value])=>[key,{loaded:value.loaded,error:value.error,animated:!!value.renderer}]))})});
})();
