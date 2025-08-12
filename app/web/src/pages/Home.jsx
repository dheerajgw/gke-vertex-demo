export default function Home({health}){
  return (<section><h2>Home</h2><pre>{JSON.stringify(health,null,2)}</pre></section>)
}
