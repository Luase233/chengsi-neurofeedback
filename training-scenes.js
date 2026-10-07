/* Quiet training backgrounds. Source photographs are kept unmodified on disk.
   Only masked sky/water texture coordinates move; image luminance, terrain and
   camera position never follow EEG, music, or time. No sound is created here. */
(() => {
  'use strict';
  const MODES=Object.freeze([
    {id:'lake-trees',label:'湖岸 · 微动自然景色'},
    {id:'lake-house',label:'湖屋 · 微动自然景色'},
    {id:'rings-still',label:'完整三圈 · 静止'},
    {id:'rings-feedback',label:'三圈 · 动态反馈（演示）'}
  ]);
  const normalize=value=>MODES.some(item=>item.id===value)?value:'lake-trees';
  const definitions={
    'lake-trees':{
      src:'assets/visuals/training-lake-trees.jpg',
      // Lower mask edges remain above the mountains, then feather inward.
      sky:[[0,0],[1,0],[1,.259],[.94,.265],[.88,.265],[.82,.278],[.77,.301],[.70,.311],[.64,.333],[.58,.343],[.54,.36],[.49,.353],[.46,.365],[.40,.35],[.36,.34],[.30,.323],[.24,.325],[.20,.307],[.17,.302],[.14,.3],[.10,.30],[.077,.294],[.047,.308],[.026,.3],[0,.315]],
      // Water begins below all reeds, trees and shoreline.
      water:[[0,.789],[1,.789],[1,1],[0,1]]
    },
    'lake-house':{
      src:'assets/visuals/training-lake-house.jpg',
      sky:[[0,0],[1,0],[1,.15],[.965,.15],[.951,.32],[.93,.32],[.923,.46],[.87,.48],[.82,.478],[.75,.482],[.71,.475],[.692,.454],[.657,.428],[.40,.428],[.37,.476],[.3,.48],[.22,.49],[.13,.465],[0,.432]],
      // Keep the house, foreground reeds and centre-left bushes motionless.
      water:[[.385,.673],[.686,.673],[.697,.604],[.873,.604],[.856,.745],[.905,.777],[.899,.929],[.87,1],[.38,1],[.385,.921],[.402,.835]]
    }
  };
  const entries=new Map();
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
      maskCtx.save();maskCtx.globalCompositeOperation='lighter';maskCtx.filter='blur(10px)';
      maskCtx.drawImage(layer,0,0);maskCtx.restore();
    }
    return mask;
  }
  function createRenderer(image,definition){
    const surface=document.createElement('canvas');
    surface.width=Math.min(1440,image.naturalWidth);
    surface.height=Math.round(surface.width*image.naturalHeight/image.naturalWidth);
    const gl=surface.getContext('webgl',{alpha:false,antialias:false,preserveDrawingBuffer:true,powerPreference:'low-power'});
    if(!gl)return null;
    function shader(type,source){
      const result=gl.createShader(type);gl.shaderSource(result,source);gl.compileShader(result);
      if(!gl.getShaderParameter(result,gl.COMPILE_STATUS))throw new Error('Scene shader unavailable');
      return result;
    }
    try{
      const program=gl.createProgram();
      gl.attachShader(program,shader(gl.VERTEX_SHADER,'attribute vec2 position; varying vec2 uv; void main(){ uv=vec2((position.x+1.0)*0.5,(1.0-position.y)*0.5); gl_Position=vec4(position,0.0,1.0); }'));
      gl.attachShader(program,shader(gl.FRAGMENT_SHADER,`
        precision mediump float;
        varying vec2 uv;
        uniform sampler2D photograph;
        uniform sampler2D regions;
        uniform vec2 pixels;
        uniform float seconds;
        void main(){
          vec2 area=texture2D(regions,uv).rg;
          // Cloud drift: at most 5 original-image pixels over an 80–160 s arc.
          // Water: <1 px, smooth long waves, no sparkle or brightness modulation.
          vec2 cloud=vec2(sin(seconds*.039+uv.y*1.3)*5.0,sin(seconds*.027+uv.x*2.0)*.8);
          float depth=smoothstep(.66,1.0,uv.y);
          vec2 water=vec2(sin(uv.y*70.0-seconds*.24+uv.x*3.0)*.85,
            sin(uv.y*48.0+seconds*.19+uv.x*7.0)*.38)*depth;
          vec2 offset=(cloud*area.r+water*area.g)/pixels;
          gl_FragColor=texture2D(photograph,clamp(uv+offset,vec2(.0005),vec2(.9995)));
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
      texture(image,0,'photograph');texture(maskImage(definition,surface.width,surface.height),1,'regions');
      gl.uniform2f(gl.getUniformLocation(program,'pixels'),image.naturalWidth,image.naturalHeight);
      const seconds=gl.getUniformLocation(program,'seconds');
      surface.addEventListener('webglcontextlost',event=>event.preventDefault());
      return {surface,render(time){
        if(gl.isContextLost())return false;
        gl.viewport(0,0,surface.width,surface.height);gl.uniform1f(seconds,time);
        gl.drawArrays(gl.TRIANGLES,0,3);return true;
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
    if(motion)timeline+=elapsed;
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
