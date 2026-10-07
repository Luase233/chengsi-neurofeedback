/* A dedicated fabric texture with real-time polar flow. Its three audio inputs
   are independent of EEG acquisition; this is a presentation-layer material. */
(function(root){
  'use strict';
  var source=document.getElementById('resonance-texture-data');
  if(!source)return;
  var url;try{url=JSON.parse(source.textContent);}catch(e){return;}
  if(!url)return;
  var textureImage=new Image(),ready=false,scenes=new WeakMap();
  textureImage.onload=function(){ready=true;root.dispatchEvent(new Event('resonance-material-ready'));};textureImage.src=url;
  var vertex='attribute vec2 position; varying vec2 uv; void main(){uv=position*.5+.5;gl_Position=vec4(position,0.,1.);}';
  var fragment=`precision highp float;
    varying vec2 uv;
    uniform sampler2D silk;
    uniform float time;
    uniform float gain;
    uniform float openness;
    uniform float energy;
    uniform float intensity;
    void main(){
      vec2 p=(uv-.5)*2.;
      float r=length(p), a=atan(p.y,p.x);
      float flow=time*.22;
      float fold=.48+openness*.52;
      float radial=1.+fold*intensity*(.024*sin(a*5.+flow)+.014*sin(a*9.-flow*.7)+.008*sin(a*13.+flow*.37));
      float bend=.016*sin(a*4.-flow*.6)+.012*sin(r*12.+flow*.55);
      float rotation=time*.027;
      float theta=a+rotation+bend*intensity;
      float breath=1.+.011*sin(flow*1.7)+energy*.008;
      // Stretch around the fabric's annular centre, rather than scaling only its
      // brightness. A quiet atmosphere remains a legible, narrowly folded silk.
      float clothWidth=.63+.37*openness;
      float sampleRadius=.72+(r*radial/breath-.72)/clothWidth;
      vec2 texUV=vec2(cos(theta),sin(theta))*max(0.,sampleRadius)*.5+.5;
      vec3 base=texture2D(silk,texUV).rgb;
      float edgeFade=1.-smoothstep(.91,1.01,r);
      float shimmer=.95+.05*sin(a*7.+flow+r*5.);
      base*=gain*shimmer*(1.+energy*.13)*edgeFade;
      gl_FragColor=vec4(base,1.);
    }`;
  function build(){
    var canvas=document.createElement('canvas'),gl=canvas.getContext('webgl',{alpha:false,antialias:false,premultipliedAlpha:false,preserveDrawingBuffer:true});
    if(!gl)return null;
    function shader(type,src){var s=gl.createShader(type);gl.shaderSource(s,src);gl.compileShader(s);if(!gl.getShaderParameter(s,gl.COMPILE_STATUS))return null;return s;}
    var vs=shader(gl.VERTEX_SHADER,vertex),fs=shader(gl.FRAGMENT_SHADER,fragment);if(!vs||!fs)return null;
    var program=gl.createProgram();gl.attachShader(program,vs);gl.attachShader(program,fs);gl.linkProgram(program);if(!gl.getProgramParameter(program,gl.LINK_STATUS))return null;
    gl.useProgram(program);var buffer=gl.createBuffer();gl.bindBuffer(gl.ARRAY_BUFFER,buffer);gl.bufferData(gl.ARRAY_BUFFER,new Float32Array([-1,-1,1,-1,-1,1,-1,1,1,-1,1,1]),gl.STATIC_DRAW);
    var pos=gl.getAttribLocation(program,'position');gl.enableVertexAttribArray(pos);gl.vertexAttribPointer(pos,2,gl.FLOAT,false,0,0);
    var texture=gl.createTexture();gl.bindTexture(gl.TEXTURE_2D,texture);gl.pixelStorei(gl.UNPACK_FLIP_Y_WEBGL,true);gl.texImage2D(gl.TEXTURE_2D,0,gl.RGB,gl.RGB,gl.UNSIGNED_BYTE,textureImage);
    gl.texParameteri(gl.TEXTURE_2D,gl.TEXTURE_MIN_FILTER,gl.LINEAR);gl.texParameteri(gl.TEXTURE_2D,gl.TEXTURE_MAG_FILTER,gl.LINEAR);gl.texParameteri(gl.TEXTURE_2D,gl.TEXTURE_WRAP_S,gl.CLAMP_TO_EDGE);gl.texParameteri(gl.TEXTURE_2D,gl.TEXTURE_WRAP_T,gl.CLAMP_TO_EDGE);
    gl.uniform1i(gl.getUniformLocation(program,'silk'),0);
    var uniforms={};['time','gain','energy','intensity','openness'].forEach(function(k){uniforms[k]=gl.getUniformLocation(program,k);});
    return{canvas:canvas,gl:gl,uniforms:uniforms};
  }
  root.drawResonanceSilk=function(ctx,radius,time,ambient,energy,opts){
    if(!ready)return false;
    var data=scenes.get(ctx);
    if(data===undefined){data=build();scenes.set(ctx,data||null);}
    var size=radius*2.48,visible=.50+.50*Math.pow(Math.max(0,ambient),.45),valid=opts.valid!==false;
    if(!valid)visible*=.28;
    if(data){
      // Resolution stays in a fixed tier as audio changes the fabric's size.
      // Resizing this canvas every frame reallocates the WebGL drawing buffer.
      var px=opts.compact?512:1024;
      if(data.canvas.width!==px){data.canvas.width=px;data.canvas.height=px;data.gl.viewport(0,0,px,px);}
      var gl=data.gl,u=data.uniforms;
      gl.uniform1f(u.time,time);gl.uniform1f(u.gain,visible*.94);gl.uniform1f(u.openness,Math.max(0,Math.min(1,ambient)));gl.uniform1f(u.energy,energy);gl.uniform1f(u.intensity,opts.intensity||1);gl.drawArrays(gl.TRIANGLES,0,6);
      ctx.drawImage(data.canvas,-size/2,-size/2,size,size);
    }else{
      ctx.save();ctx.rotate(time*.025);ctx.globalAlpha=visible;ctx.drawImage(textureImage,-size/2,-size/2,size,size);ctx.restore();
    }
    return true;
  };
})(typeof window!=='undefined'?window:globalThis);
